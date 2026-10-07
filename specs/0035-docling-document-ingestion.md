# Spec-0035: Document parsing for RAG ingestion (Docling via `docling-serve`)

- **Status:** In progress
- **Linked ADR:** [ADR-0036](../docs/adr/0036-document-parser-seam.md) — new
  `DocumentParser` seam, new `DocumentParseError`, out-of-process provider.
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added — document parsing for RAG ingestion (spec-0035)`
- **Plans:** [delivery plan](../docs/plans/20261006T233919Z-docling-document-ingestion-plan.md) ·
  [implementation runbook](../docs/plans/20261007T004835Z-docling-implementation-plan.md)

## Problem

`mangomas rag ingest` reads only UTF-8 `*.txt` / `*.md` (`rag/loader.py`), so the
documents operators actually hold — PDFs, Word, PowerPoint, Excel — cannot be
retrieved. Docling converts those formats to Markdown with tables and reading
order preserved. This spec adds an opt-in parsing seam with a `docling-serve`
(out-of-process) provider, without changing anything when the feature is off,
and without letting a parse failure damage an existing index.

## Requirements

- **R1 — Seam.** A `@runtime_checkable` `DocumentParser` protocol in
  `adapters/parsers/base.py`: `async parse(*, filename: str, content: bytes) ->
  ParsedDocument` and `async aclose() -> None`. `ParsedDocument(text: str,
  pages: int | None = None, partial: bool = False)` is a frozen dataclass of
  primitives; the adapter package never imports `rag/`.
- **R2 — Settings.** A `ParserSettings` group (`MANGOMAS_PARSER__*`, table
  below), `enabled=false` by default; every tunable is a `DEFAULT_*` constant.
  Validation fails fast at settings load: limits positive, client timeout
  strictly greater than document timeout, suffixes normalised to lower-case with
  a leading dot.
- **R3 — Provider.** `docling_serve` posts one file to `{base_url}/v1/convert/file`
  and never calls any other conversion endpoint. It sends a generated filename
  (`document<suffix>`), `from_formats` pinned to the file's format, `to_formats=md`,
  the document timeout and page cap, and `X-Api-Key` **only** when a key is
  configured (`secret_ref` resolved through `SecretsProvider` overrides
  `api_key`).
- **R4 — Limits before I/O.** Files over `max_file_bytes` and OOXML archives
  exceeding the entry-count or compression-ratio ceilings are refused before any
  network call; responses over `max_response_bytes` are refused.
- **R5 — Status mapping.** `success` → text; `partial_success` → text with
  `partial=True` and a warning; `failure`, `skipped` and any unknown status →
  `DocumentParseError`; HTTP 401/403 → `ConfigError`; other HTTP errors,
  timeouts and transport errors → `DocumentParseError`. No raw `httpx`
  exception escapes.
- **R6 — Loader.** A new async generator `iter_documents(path, *, parser=None,
  settings=None)` yields `RawDoc` or `ParseFailure` one document at a time.
  `load_documents` and `_load_documents_sync` are unchanged. Files whose
  resolved path escapes the ingest root (symlinks) are skipped with a warning.
  Parsed-path single-file sources are the file name, never an absolute path.
- **R7 — Pipeline.** `IngestionPipeline` gains optional `parser` and
  `parser_settings` keywords. A `ParseFailure` under `on_error=skip` is logged,
  counted in `IngestReport.skipped_documents`, and **never** reaches
  `_replace_source`; under `on_error=fail` it raises. A non-empty file whose parse
  yields empty text is a `ParseFailure`. Parsed documents are chunked by a
  whitespace-preserving window (`chunk_lines`) so tables keep their newlines;
  text documents use `chunk_text` exactly as today.
- **R8 — Metadata.** Text-document chunk metadata stays `{source, index}`.
  Parsed-document chunks add `parser`, `parse_status`, `chunker`, `chunk_words`
  and `embedding_model`; reserved keys `source` and `index` are written last and
  always win; values are Chroma-legal scalars (`None` dropped, scalar lists joined,
  mappings dropped).
- **R9 — Retrieval framing.** `RetrievalTool` renders parser-derived chunks inside
  an untrusted-document delimiter; text-file chunks render byte-identically to
  today.
- **R10 — Composition & CLI.** `composition/parser.py` builds the parser only
  when enabled and attaches it to `AgentContext.extras`; `mangomas rag ingest`
  passes it to the pipeline, closes it in `finally`, and appends ` skipped=N`
  only when `N > 0`. CLI options and help pins are unchanged.
- **R11 — Observability.** Spans `rag.parse` (pages, bytes, status) per parsed
  document; structured events `parser_partial_success`,
  `rag_document_parse_failed`, `rag_symlink_escape`, `rag_chunk_over_budget`;
  logs carry an allow-list of fields only — never document text, original
  paths beyond the source id, serve error bodies, or credentials.
- **R12 — Measurement gate.** A `RUN_DOCLING`-gated bake-off harness with pure,
  unit-tested metrics (recall@k, MRR@k, nDCG@k, context precision, parse recall,
  document-cluster bootstrap). Structure-aware chunking is adopted only under the
  pre-registered rule below.
- Must remain **additive & default-OFF**: with `MANGOMAS_PARSER__ENABLED=false`
  the ingest of `.txt`/`.md` trees is byte-identical (report, ids, metadata, CLI
  output) and no parser or HTTP client is constructed.

## Scenarios (WHEN/THEN)

- WHEN the parser is disabled THEN a tree containing `.txt`, `.md` and `.pdf`
  ingests exactly as before (the `.pdf` ignored) AND no parser is built.
- WHEN `a.pdf` fails to parse under `on_error=skip` THEN `a.pdf`'s existing
  vectors and count are unchanged AND `skipped_documents == 1`.
  WHEN an empty `.md` is ingested THEN its vectors are still purged (today's
  behaviour, the opposite direction).
- WHEN a non-empty `.pdf` parses to empty text THEN it is a parse failure, not a
  purge.
- WHEN `on_error=fail` and file N fails THEN ingest raises AND files 1..N-1 are
  persisted.
- WHEN a file exceeds `max_file_bytes` (boundary `n+1`) THEN it is refused with
  zero network calls; WHEN it is exactly `n` THEN it is sent.
- WHEN no API key is configured THEN no `X-Api-Key` header is sent; WHEN one is
  configured THEN it is sent; in neither case does the key appear in logs,
  exception text or span attributes.
- WHEN a symlink inside the root points outside it THEN the file is skipped;
  WHEN it points inside the root THEN it is followed.
- WHEN a parsed Markdown table is chunked THEN the stored chunk keeps its line
  breaks and separator row.
- WHEN document metadata contains `source`/`index` THEN the pipeline's values win.
- WHEN the suffix is not in `allowed_suffixes` THEN the file is not sent; WHEN it
  is (any case) THEN it is.

## Config / env additions

All defaults are provisional pending the verification spike (runbook PR 1) and
the bake-off (PR 2); they live as `DEFAULT_*` constants in `config/parser.py`.

| Env var (`MANGOMAS_*`) | Default | Purpose |
|---|---|---|
| `MANGOMAS_PARSER__ENABLED` | `false` | Parse non-text documents during `rag ingest` |
| `MANGOMAS_PARSER__PROVIDER` | `docling_serve` | Parser registry entry |
| `MANGOMAS_PARSER__BASE_URL` | `http://localhost:5001` | docling-serve base URL |
| `MANGOMAS_PARSER__API_KEY` | _(none)_ | Optional `X-Api-Key` (defence in depth) |
| `MANGOMAS_PARSER__SECRET_REF` | _(none)_ | `SecretsProvider` ref that overrides `API_KEY` |
| `MANGOMAS_PARSER__TIMEOUT_SECONDS` | `300.0` | HTTP client timeout (must exceed the document timeout) |
| `MANGOMAS_PARSER__DOCUMENT_TIMEOUT_SECONDS` | `240.0` | Server-side per-document conversion budget |
| `MANGOMAS_PARSER__ALLOWED_SUFFIXES` | `[".pdf",".docx",".pptx",".xlsx"]` | Formats sent to the parser |
| `MANGOMAS_PARSER__MAX_FILE_BYTES` | `52428800` | Refuse larger files before upload |
| `MANGOMAS_PARSER__MAX_PAGES` | `500` | Page cap sent to the parser |
| `MANGOMAS_PARSER__MAX_RESPONSE_BYTES` | `20971520` | Refuse larger parser responses |
| `MANGOMAS_PARSER__MAX_ZIP_ENTRIES` | `10000` | OOXML archive entry ceiling |
| `MANGOMAS_PARSER__MAX_ZIP_RATIO` | `100.0` | OOXML uncompressed/compressed ceiling |
| `MANGOMAS_PARSER__ON_ERROR` | `skip` | `skip` (log, count, never purge) or `fail` |
| `MANGOMAS_PARSER__DO_OCR` | `false` | Ask the parser to OCR scanned pages |
| `MANGOMAS_PARSER__PARSED_CHUNK_WORDS` | `300` | Window size for parsed documents |
| `MANGOMAS_PARSER__EMBED_MAX_TOKENS` | _(none)_ | Warn when a chunk likely exceeds the embedder limit |

## Protocol / contract impact

- New protocol: `DocumentParser` (`adapters/parsers/base.py`).
- New error type: `DocumentParseError` (`errors.py`, code `document_parse_error`,
  `_ERROR_STATUS` → 502). Protected path: isolated commit with
  `BREAKING-CHANGE: src/mangomas/errors.py — …`.
- Registry addition: `parser` registry, provider `docling_serve`.
- Additive changes: `IngestionPipeline(parser=…, parser_settings=…)`,
  `IngestReport.skipped_documents: int = 0`, `RawDoc.metadata` (defaulted),
  `rag.loader.iter_documents`, `rag.chunker.chunk_lines`.
- Test-infrastructure: `docling` pytest marker (`pyproject.toml`, governance
  surface — isolated trailer commit), `RUN_DOCLING` gate.

## Backwards-compatibility

- Flag off: byte-identical ingest of text trees; `load_documents`,
  `_load_documents_sync`, `chunk_text`, CLI options and help, and text-chunk
  retrieval output are unchanged. Pinned tests (`tests/rag/test_loader.py`,
  `tests/regression/test_sdlc_gate_defects.py`, `tests/test_cli_rag.py`,
  `tests/constants/cli.py`) pass untouched.
- New dataclass fields are defaulted, so existing constructors and equality
  assertions hold.
- Disabling the parser after use does not purge PDF-derived vectors (documented
  rollback note).

## Pre-registered decision rule (structure-aware chunking)

Fixed before any measurement. Adopt a chunker or parser variant only if, on the
frozen test slice: (a) the 95 % document-cluster-bootstrap CI lower bound of
Δrecall@5 is > 0, (b) no stratum regresses by more than 5 points, and (c) parse
p95 latency and cost stay within the ceilings recorded in the bake-off report. A
CI that spans zero is reported as **inconclusive**, never as a negative result.

## Test plan

- Unit (default suite): `tests/adapters/parsers/`, `tests/composition/test_parser.py`,
  `tests/rag/test_loader_iter.py`, `tests/rag/test_pipeline_parsed.py`,
  `tests/rag/test_chunker.py`, `tests/rag/test_retrieval.py`, `tests/test_cli_rag.py`,
  `tests/test_config.py`, `tests/test_errors.py`, `tests/rag/bakeoff/test_metrics.py`.
  Fakes: `FakeDocumentParser` in `tests/fakes.py`; HTTP via `respx`.
- Hypothesis: loader parity and ordering, `chunk_lines` invariants, metadata
  flattening.
- Gated: `RUN_DOCLING=1 make docling-bakeoff` (needs docling-serve and a real
  embedder; recorded in `HOSTED_RUNNER_INFEASIBLE`).
- Mutation proofs: no-purge (two-sided), suffix allow-list (two-sided), reserved
  keys (both), size pre-check, API-key header (two-sided), on_error inversion,
  build-when-disabled, symlink containment, empty-parse-as-failure, line
  preservation.
- Coverage: global 95 %, `adapters` 85 %, `rag` 95 %.

## Acceptance criteria

- [ ] Feature off by default → no behaviour change (golden test proves it).
- [ ] Feature on via env → parsed documents ingested, failures skipped without
      purging (tests prove it).
- [ ] `make gate` clean (ruff, format, mypy, import contracts, frontmatter,
      protected paths, tests, coverage floors).
- [ ] CHANGELOG updated; ADR-0036 Accepted before merge.
- [ ] Verification spike results recorded (serve auth, endpoints, limits,
      version floor, image digest) — **pending: needs a Docker host**.
