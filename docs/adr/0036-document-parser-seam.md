# ADR-0036: Document parser seam, out-of-process first

## Status

Proposed

## Context

`mangomas rag ingest` reads only `.txt`/`.md`. Adding PDF and Office formats
means parsing untrusted binaries, and Docling, the leading parser, pulls in
torch and had four 2026 CVEs in its non-PDF backends (XXE, LaTeX path
traversal, HTML rendering, HTML URI handling). The ingest corpus is global and
operator-fed (ADR-0033). The owner approved the decisions below on 2026-10-07.

## Decision

1. **Seam.** `@runtime_checkable DocumentParser` in
   `adapters/parsers/base.py`: `async parse(*, filename, content: bytes) ->
   ParsedDocument` and `async aclose()`. `ParsedDocument(text, pages=None,
   partial=False)` is a frozen primitives-only dataclass, like `VectorMatch`, so
   adapters never import `rag/`. Composition holds a `parser` registry. The first
   provider is `docling_serve`. `MANGOMAS_PARSER__ENABLED=false` by default, and
   with it off `.txt`/`.md` ingest is byte-identical to today.
2. **Out-of-process first.** `docling_serve` adds no torch or pip dependency to
   the main install or CI, and it isolates untrusted parsing. An in-process
   `docling_local` provider is deferred: opt-in and CLI-only.
3. **Service auth.** The primary control is an IAM ID token: `roles/run.invoker`,
   no unauthenticated invoke, internal ingress and a dedicated SA. `X-Api-Key` is
   only defence-in-depth. Only `/v1/convert/file` is called. `/v1/convert/source`
   is an SSRF primitive and is never called.
4. **Operator privilege.** Ingest is an operator action, and the vector corpus
   is **not** tenant-isolated. No upload endpoint and no parse-document tool will
   exist until a tenancy ADR supersedes this point.
5. **Failure policy.** `on_error=skip` (the default) logs the failure, counts it
   and never purges the source's existing vectors. `on_error=fail` raises. Parsing
   streams one document at a time, so files 1..N-1 are already replaced when file
   N fails. A non-empty file whose parse returns empty text counts as a parse
   failure. A genuinely empty `.txt`/`.md` file is still purged, as today.
6. **Error type.** A new `DocumentParseError` (`document_parse_error`) maps to
   HTTP 502. v1 is CLI-only, but the status walk in `tests/test_errors.py`
   requires every subclass to have a mapping. A 401 or 403 from the parser
   raises `ConfigError`, because bad credentials are a configuration problem.
7. **Limits.** These are settings with `DEFAULT_*` constants: max file bytes,
   max pages, max response bytes, OOXML zip entry-count and compression-ratio
   ceilings, and a document timeout shorter than the client timeout. The allowed
   formats are `.pdf .docx .pptx .xlsx`. HTML is excluded because of its CVE
   history.
8. **Untrusted content.** Parsed text is treated as data. `RetrievalTool` wraps
   parser-derived chunks in an explicit untrusted-document delimiter. Text-file
   chunks render exactly as today. Hidden-text injection remains a risk, and the
   bake-off measures it.
9. **Residency.** Documents leave the host for the serve service, which runs in
   the same region with body logging off.
10. **Inherited debt.** The CLI builds `IngestionPipeline` directly.
    `composition/parser.py` builds the parser and attaches it to
    `AgentContext.extras`. The pipeline receives it through a new optional
    `parser=` keyword. Building the pipeline in composition is a follow-up.
11. **Rollback.** Set `MANGOMAS_PARSER__ENABLED=false`. There is no migration,
    and PDF-derived vectors stay until removed with a manual `delete_by_source`.
12. **Not decided here.** Structure-aware chunking depends on the bake-off rule
    pre-registered in spec-0035.

## Consequences

### Positive

- PDF/Office ingest ships with no new main-install dependency and with the
  parser isolated.
- "Parser down" is distinguishable from "bad config".

### Negative / Trade-offs

- Operators must run and secure a separate service of about 4 GB.
- `fail` mode can leave a partially replaced corpus.
- `errors.py` changes, which needs a `BREAKING-CHANGE` commit.
- Pre-existing hazard, now costlier: changing the embedding model raises a
  dimension mismatch after the source has already been deleted. A
  fail-before-delete guard needs a new store capability protocol, because
  `VectorStoreRepository` cannot grow a method. This goes to a follow-up spec.

### Neutral

- Production `service.yaml` likely needs no `MANGOMAS_PARSER__*` entries.

## Alternatives Considered

- **In-process first:** rejected. It brings torch into the main install and
  gives up isolation.
- **Reuse `ConfigError` / `PersistenceError`:** rejected. Both mislabel an
  upstream parser failure.
- **LangChain/LlamaIndex loaders:** rejected. They are a framework dependency
  that crosses the protocol seam.
- **No new error:** rejected for the same mislabelling reason.

## Verification pending (A1 spike)

- serve's default auth
- whether `/v1/convert/source` can be disabled
- the container user
- whether `md_content` drops page headers and footers
- exact CVE IDs and the version floor, taken from the advisories
- the pinned image digest

## References

- Spec: `specs/0035-docling-document-ingestion.md`
- Plans: `docs/plans/20261006T233919Z-docling-document-ingestion-plan.md`,
  `docs/plans/20261007T004835Z-docling-implementation-plan.md`
- Code: `src/mangomas/rag/{loader,pipeline,retrieval}.py`,
  `src/mangomas/cli/commands/rag.py:59`
- Related ADRs: ADR-0033 (boundary honesty), ADR-0019 (facades), ADR-0021
  (protected paths)
