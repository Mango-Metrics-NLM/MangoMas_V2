# Docling document ingestion — delivery plan

- **Branch:** `feat/initial-release` (cut a dedicated `feat/docling-ingestion` branch before M1)
- **Date:** 2026-10-06
- **Target release:** rolling (additive, default-OFF)
- **Status:** Draft
- **Specs:** spec-0035 (to be drafted in M0 from `specs/TEMPLATE.md`)
- **ADRs:** ADR-0036 (to be drafted in M0: parser seam, out-of-process default, error type, tenancy honesty)

## Executive summary

Ship PDF / Office / HTML ingestion for `mangomas rag ingest` behind a new
`DocumentParser` protocol, **parsing first and chunking later**. The first
provider is `docling_serve` (httpx to a separately deployed docling-serve): it
needs no new pip dependency, so CI never installs torch, the tests are pure
`respx`, and `pyproject.toml` (a governance-surface path) stays untouched until
a later milestone. Structure-aware chunking is deliberately sequenced *after*
parsing and *behind a measurement*, because it touches the pipeline, the chunk
metadata schema and the retrieval output, and its benefit is unproven on this
corpus. The constraint that shaped the order: every step must leave
`MANGOMAS_PARSER__ENABLED=false` byte-identical to today's `.txt`/`.md` path.

## Findings that shaped this plan

Web-verified (Oct 2026): docling 2.134.0, MIT; docling-serve exposes
`/v1/convert/file` (+ `/async`), `X-Api-Key` auth, `document_timeout`, and
returns `status: success|partial_success|skipped|failure` with
`md_content`/`json_content`; the CPU image is ~4.4 GB and sizing guidance is
4 vCPU / 8–16 GB for production. Four 2026 CVEs hit Docling's non-PDF backends
(XXE in METS-GBS, path traversal in LaTeX, HTML Playwright rendering, HTML
URI/path handling), fixed in 2.91.0 / 2.94.0. `ocr_engine` is deprecated in
serve in favour of `ocr_preset` (and issue #567 reports it being ignored).

Repo-verified (this checkout):

- `RawDoc` is `(source, text)`; the pipeline writes only `{source, index}`
  metadata and embeds the same string it stores (`rag/pipeline.py:146,220`).
- `load_documents` returns a full list and is used by tests
  (`tests/rag/test_loader.py`, `tests/regression/test_sdlc_gate_defects.py`) —
  its signature is permanent; add an iterator beside it, never change it.
- A document that yields no chunks **purges** its prior vectors
  (`_replace_source` with empty batches). A *parse failure* must never reach
  that path or one corrupt PDF silently deletes a good index entry.
- `VectorStoreRepository` has no `get`/fingerprint primitive; adding a method
  breaks implementers (`adapters/CLAUDE.md`), so content-hash skipping needs a
  *new capability protocol*, not a method.
- Import-linter: `workflow`, `eval`, `rag`, `cognitive` are mutually
  independent (indirect imports count). `rag/` may see the parser protocol only
  under `TYPE_CHECKING`, as it does for embeddings/vector.
- `pyproject.toml` and `errors.py` both require a `BREAKING-CHANGE: <path> —
  <rationale>` trailer (bare path, first token, one per path).
- The vector collection is global (`MANGOMAS_VECTOR__COLLECTION`); tenancy
  partitions conversation turns only, so RAG is **not** tenant-isolated. Any
  upload-style API is blocked on that (ADR-0033 boundary honesty).
- Install docs target Windows/PowerShell; docker-for-serve is friction for the
  local-first user, which is why an in-process provider (M5) stays on the plan.

## PR A — Decision records and the measurement harness (spec-0035)

### Milestone A0 — Spec, ADR, golden set ✅-when-merged

- **Failing test first:** none — docs-only. Instead, the *gate for B/C*: a
  gated test `tests/rag/test_parser_bakeoff.py` (`RUN_DOCLING=1`) that ingests
  a small golden corpus (10–20 real documents: ≥3 table-heavy PDFs, 1 scanned
  PDF, 1 DOCX, 1 PPTX) and asserts hit@k on hand-written
  (question → expected source [+ heading]) pairs using the **real**
  `Retriever`. It runs word-chunking today to set the baseline number.
- **Depends on:** nothing — parallel-safe.
- Draft `specs/0035-docling-document-ingestion.md` and
  `docs/adr/0036-document-parser-seam.md` (use `mango-adr-author`). The ADR
  must record: out-of-process default; format allow-list; `on_error` policy;
  new `DocumentParseError`; "RAG is not tenant-isolated, so no upload API";
  retrieved/parsed text is untrusted data (prompt-injection surface, incl.
  hidden/white-on-white PDF text).
- Optional comparison column in the bake-off: `pypdfium` backend, and one
  lighter parser (e.g. pymupdf4llm) on the text-native PDFs — the claim that
  Docling is worth its weight is currently unmeasured.

## PR B — Parsing seam, behind a flag (spec-0035)

### Milestone B1 — Protocol, fake, settings, error

- **Failing test first:** `tests/adapters/parsers/test_base.py` —
  `isinstance(FakeDocumentParser(), DocumentParser)`; `tests/test_config.py` —
  defaults (`enabled=False`) and validators; `tests/test_errors.py` status walk
  fails until `DocumentParseError` is mapped.
- **Depends on:** A0 (ADR accepted).
- `src/mangomas/adapters/parsers/base.py`: `@runtime_checkable DocumentParser`
  (`async parse(self, *, filename: str, content: bytes) -> ParsedDocument`,
  `aclose`) and frozen `ParsedDocument(text, pages: int | None, metadata)` —
  primitives only, so the adapter never imports `rag/` (mirror `VectorMatch`).
- `src/mangomas/config/parser.py`: `ParserSettings` — `enabled`, `provider`
  (`docling_serve`), `base_url`, `api_key`, `secret_ref` (mirror
  `MANGOMAS_LLM__SECRET_REF` precedence), `timeout_seconds`,
  `document_timeout_seconds`, `allowed_suffixes`, `max_file_bytes`,
  `on_error` (`skip|fail`), `ocr` (bool/preset). Re-export through
  `config/__init__.py`; add to `_root.py::Settings`; extend
  `tests/test_import_compat.py` facade-identity contract.
- `errors.py`: `DocumentParseError(MangomasError)` code
  `document_parse_error`; map in `api/errors.py::_ERROR_STATUS`; extend
  `tests/test_errors.py` (skill `mango-error`, agent
  `mango-error-taxonomy-dev`). **Commit trailer:**
  `BREAKING-CHANGE: src/mangomas/errors.py — add DocumentParseError`.
- `tests/fakes.py`: `FakeDocumentParser` (scriptable text/raise/latency).
- `.env.example` + the `AGENTS.md` config table: every new var, both
  directions (`tests/deploy/test_env_example_contract.py`).

### Milestone B2 — docling-serve adapter

- **Failing test first:** `tests/adapters/parsers/test_docling_serve.py` with
  `respx`: success → text; `partial_success` → text + warning log; `failure`
  and `skipped` → `DocumentParseError`; HTTP 5xx / timeout / connect error →
  typed error (never a raw `httpx` exception — reuse `_http_errors.py`
  translation where it fits, else add a parser variant); `X-Api-Key` header
  sent only when a key is set; malformed JSON → typed error with truncated
  `detail`; oversize file refused **before** any network call.
- **Depends on:** B1.
- `adapters/parsers/docling_serve.py`: `OpenAICompatHTTPClient` is LLM-shaped,
  so extract only the lifecycle idea (injected `httpx.AsyncClient`,
  `rstrip("/")` base URL, `_request`/translate) rather than subclassing blindly
  — decide in review whether a small shared base is warranted (mango-decompose
  guidance applies if it grows).
- Request: multipart upload to `/v1/convert/file` with `to_formats=md`,
  `document_timeout`, `ocr_preset`/`do_ocr` (**not** `ocr_engine`).
- Registry: `composition/parser.py` + `parsers` entry in `_registries.py`
  (provider names table in `composition/CLAUDE.md` must be updated or
  `tests/tooling/test_directory_claude_md.py` fails). Constructs nothing unless
  `enabled`. Update `adapters/CLAUDE.md` Protocol table too.
- Secrets: API key resolved via `SecretsProvider`, never logged; the `detail`
  field carries status/length only, never document bytes.

### Milestone B3 — Loader + pipeline integration (default-OFF)

- **Failing test first:** (1) `test_loader_iter`: `iter_documents` yields the
  same `RawDoc`s as `load_documents` for text trees (parity, so the old API is
  provably unchanged); (2) with a `FakeDocumentParser` a directory containing
  `a.pdf`, `b.md` yields both, sorted deterministically; (3) **parse failure
  of `a.pdf` does not delete `a.pdf`'s existing vectors** (pre-seed a
  `FakeVectorStore`, run, assert still present); (4) parser off → ingest of a
  `.pdf` is skipped exactly as today (suffix filter unchanged).
- **Depends on:** B2 (or B1 + the fake — parallel-safe with B2).
- `rag/loader.py`: add `iter_documents(path, *, parser=None, ...)` async
  generator; `load_documents` stays and delegates for text-only (permanent
  facade, ADR-0019 style). Parsing happens **per document, not up front**, so
  memory is bounded by one document and a failure on file N cannot lose files
  1..N-1 (they are already embedded and swapped).
- Files are read with `asyncio.to_thread`; `source` stays the POSIX relative
  path (re-ingestion contract unchanged). `RawDoc` gains an optional
  `metadata: Mapping[str, Any]` with a default (additive; frozen dataclass).
- `rag/pipeline.py`: consume the iterator; `IngestReport` gains
  `skipped_documents: int = 0` (default-safe); a parse failure under
  `on_error=skip` logs `rag_document_parse_failed` (event + source + error
  code, no content), increments `skipped_documents`, and **never** calls
  `_replace_source`; under `on_error=fail` it raises. Span `rag.parse` per
  document with `rag.parse.pages`, `rag.parse.bytes`.
- Empty-path warning logic (`rag_ingest_empty`) must key off "yielded zero",
  since the count is no longer known up front.
- `cli/commands/rag.py`: help text, build the parser through composition, close
  it in the `finally`; print `skipped=` only when non-zero (keep the existing
  one-line output stable otherwise — check for CLI output snapshot tests).
- Format allow-list is enforced **twice**: our `allowed_suffixes` before the
  upload (default `.pdf .docx .pptx .xlsx .html`; **no** LaTeX/METS/XBRL/
  audio/video/email) and, in M5, Docling's own `allowed_formats`.

### Milestone B4 — Operations and docs

- **Failing test first:** `tests/deploy/` contract for any new deploy env
  entries; docs build/lint (frontmatter + markdown) if applicable.
- **Depends on:** B3.
- `deploy/`: docling-serve as a separate Cloud Run service (min 4 vCPU /
  8–16 GB, concurrency tuned low, request timeout ≥ longest `document_timeout`;
  service-to-service auth — IAM ID token vs `X-Api-Key` decided in the ADR).
  Pin `docling-serve` image to a version **≥ 2.94-equivalent** (CVE floor) and
  record the pin in `deploy/`.
- `docs/rag/` page + `mango-rag` skill update + CHANGELOG `[Unreleased]`.
- Local-dev recipe: `docker run` docling-serve-cpu; note the ~4.4 GB pull and
  Windows/Docker requirement.

## PR C — Structure-aware chunking, **gated on the A0 baseline**

### Milestone C1 — Heading/page metadata plumbing (no new chunker yet)

- **Failing test first:** pipeline test — a `RawDoc` carrying
  `metadata={"title": ...}` produces chunk metadata with only Chroma-legal
  scalars (str/int/float/bool; lists flattened to a delimited string or
  dropped, never passed raw — Chroma rejects nested values); retrieval test —
  `RetrievalTool` output includes `heading`/`page` **when present** and is
  byte-identical when absent.
- **Depends on:** B3.
- Add a `rag/chunker.py` `ChunkSpec(text, metadata)` and a `Chunker` callable
  seam; `chunk_text` itself is untouched. Pipeline merges doc-level metadata
  into each chunk's metadata after the reserved keys (`source`, `index` can
  never be overridden — test it).
- Extend `_match_to_result`/`RetrievalTool` formatting additively.

### Milestone C2 — Markdown-heading chunker (dependency-free) + measure

- **Failing test first:** golden-markdown test: nested headings →
  `heading_path` metadata, a table is never split mid-row below a size cap,
  oversize sections fall back to word windows with the **same** overlap rules.
- **Depends on:** C1.
- Pure-Python splitter over the markdown the parser already returns; page
  numbers are *not* available here (markdown lacks provenance) — acceptable
  for v1 and stated in the spec.
- Run the A0 bake-off with this chunker. **Decision gate:** adopt only if
  hit@k improves on the golden set beyond noise; otherwise stop here and
  record the negative result in the ADR.

### Milestone C3 — Docling HybridChunker spike (only if C2 leaves a gap)

- **Failing test first:** gated `RUN_DOCLING=1` test on a table-heavy PDF
  asserting page-number metadata and header-repeated table chunks.
- **Depends on:** C2 decision gate.
- Needs `json_content` from serve + `docling-core[chunking]` (much lighter
  than full `docling`, but **verify install weight and the tokenizer
  download/offline story first** — unverified). Tokenizer must match the
  *embedding* model; LM Studio's model has no guaranteed HF tokenizer, so
  either document an approximate `max_tokens`, or pack Hierarchical-chunker
  blocks by **words** to keep the existing unit. Lives behind an additive
  capability protocol in `adapters/parsers/` (not in `rag/`, which must stay
  pure). Touches `pyproject.toml` → trailer
  `BREAKING-CHANGE: pyproject.toml — add docling-core chunking extra`.
- Embedding text vs stored text: embed `contextualize()` output, store/return
  raw chunk text — a pipeline change (it currently embeds and stores the same
  string); only adopt if the bake-off shows the gain.

## PR D — In-process provider (local-first / Windows users)

### Milestone D1 — `docling_local`

- **Failing test first:** protocol-conformance + lazy-import test (module
  imports with the extra absent; error message names
  `pip install 'mangomas[docling]'`).
- **Depends on:** B3. Parallel-safe with C.
- `DocumentConverter` built **once** (model load is the cost), calls wrapped in
  `asyncio.to_thread` behind an `asyncio.Semaphore` (thread-safety of a shared
  converter is **unverified** — serialise until a test or upstream doc proves
  otherwise); `allowed_formats` restricted; `pypdfium` backend as a
  low-memory option; OCR off by default (EasyOCR ≈30 s/page on CPU).
- New extra `docling = ["docling>=2.94"]` in `pyproject.toml` (trailer
  `BREAKING-CHANGE: pyproject.toml — add docling extra`). Check for resolver
  conflicts with `chromadb` / `sentence-transformers` in a clean venv before
  merging; if they conflict, document "use `docling_serve`" and stop.
- Coverage: the lazy import helper uses the repo's `# pragma: no cover -
  requires <extra>` pattern (as in `adapters/vector/chroma.py`); the rest is
  exercised through an injected converter, so the 85 % adapters floor holds
  (`mango-coverage-audit` to confirm the denominator).

## Deferred / out of scope

- **Parse-document agent tool / `POST /documents` upload endpoint.** Blocked
  on (a) RAG not being tenant-isolated, (b) SSRF/arbitrary-read risk (Docling
  accepts URLs and paths), (c) prompt-injection from parsed content. Reopen
  only via a new ADR that supersedes the tenancy statement in ADR-0036.
- **Content-hash skip on re-ingest.** Needs a new capability protocol
  (e.g. a fingerprint-aware vector store) because `VectorStoreRepository`
  cannot grow a method; or a sidecar manifest. Worth doing once PDFs make
  re-ingest expensive — separate spec.
- **Retrieval-quality eval target.** The eval harness scores agent output, not
  retrieval; `eval` may not import `rag` (independence contract), so a
  `retrieve` target would have to go through `ToolRegistry`. The A0 gated test
  is the interim measure.
- **docling-serve async endpoints** (`/convert/file/async` + poll) — adopt if
  sync timeouts bite on large PDFs.
- **Audio/video/email/LaTeX/XBRL/METS formats**, **VLM (Granite-Docling)
  pipeline**, **MCP server in `.mcp.json`** (governance-surface edit for little
  benefit).

## Verification

```bash
make gate                                   # full pre-PR chain, CI order
python -m pytest tests/adapters/parsers tests/rag -q
make lint-imports                           # rag/eval/workflow/cognitive stay independent
make protected-paths                        # trailers present for errors.py / pyproject.toml
RUN_DOCLING=1 python -m pytest tests/rag/test_parser_bakeoff.py --no-cov -q
```

Mutation proofs (`mango-mutation-proof`) required before merge: (1) delete the
"don't purge on parse failure" branch → test B3-(3) must go red; (2) remove the
`allowed_suffixes` check → the format allow-list test must go red; (3) let a
reserved metadata key be overridden → the C1 test must go red.
