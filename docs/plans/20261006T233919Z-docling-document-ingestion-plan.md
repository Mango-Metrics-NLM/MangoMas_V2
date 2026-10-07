# Docling document ingestion — delivery plan (rev 2, peer-reviewed)

- **Branch:** `docs/docling-ingestion-plan` (implementation PRs cut from `feat/initial-release`, one branch per PR below)
- **Date:** 2026-10-06
- **Target release:** rolling (additive, default-OFF)
- **Status:** Approved 2026-10-07 (rev 2; supersedes rev 1 after a six-role review). Execution runbook: [`20261007T004835Z-docling-implementation-plan.md`](20261007T004835Z-docling-implementation-plan.md)
- **Specs:** spec-0035 (drafted in PR A1)
- **ADRs:** ADR-0036 (drafted in PR A1; must be **Accepted** before PR B1 merges)

## Executive summary

Add PDF/Office ingestion to `mangomas rag ingest` behind a `DocumentParser`
protocol, with an out-of-process `docling-serve` provider first. **Parsing ships
before any structure-aware chunking, and only parsing is unconditional**; the
chunking work (PR C) is gated on a properly powered measurement (PR A2), because
the rev-1 gate ("improves beyond noise") was not a procedure. Rev 2 differs from
rev 1 in five load-bearing ways: (1) the word chunker would have flattened every
parsed table, so line-preserving windowing moves into PR B; (2) auth, resource
limits, symlink containment and prompt-injection framing are specified instead
of deferred; (3) the gated bake-off is wired into the repo's gate-registration
contract and redesigned to be statistically meaningful; (4) PRs are split so each
is independently green and the protected-path commits are minimal; (5) every
hand-waved decision became either a concrete requirement or an explicit open
question. The constraint that still shapes everything: with
`MANGOMAS_PARSER__ENABLED=false`, ingest of `.txt`/`.md` is byte-identical to
today.

## The review board and what each found

Six independent reviews of rev 1; each verified claims against the repo. Findings
were adjudicated by me; where reviewers disagreed, the disposition says so.

| Role | Verdict | Top findings adopted |
|---|---|---|
| Principal architect | Approve with changes; 0 blockers | Parser→pipeline wiring unspecified; empty-OCR text silently purges; `DocumentParseError` is a protected edit for marginal gain; B3 too large |
| RAG engineer | 1 blocker | `chunk_text` does `split()`/`" ".join` (`rag/chunker.py:30,40`) — parsed tables lose all newlines; words≠tokens vs embedder limit; reserved-key merge order; mixed-strategy audit trail; embedding-model change purges before failing |
| Test/QA engineer | 2 blockers | Bake-off has no pass criterion and no gate wiring; B3's test conflates four assertions and lacks the opposite-direction control; ~12 missing cheap tests; 4 more mutation proofs |
| AppSec engineer | 2 blockers | Service auth undecided (open serve = unauthenticated parser + SSRF); no page/decompressed/response caps; symlink + absolute-path `source` leakage; image digest + model provenance; stored prompt injection unmitigated; HTML in default allow-list |
| Retrieval-eval scientist | 2 blockers | Decision rule not operational; 10–20 docs cannot detect a 3–8 pt gain; labels coupled to the chunker under test; hit@k alone; baseline grid incomplete; no reproducibility pins |
| Release/CI owner | 1 blocker | `RUN_DOCLING` fails `tests/deploy/test_gated_suite_homes.py` unless registered in `ENV_GATE_SUITES`/`HOSTED_RUNNER_INFEASIBLE`; serve cannot reuse `deploy/service.yaml` + `sed` + `/healthz` smoke path; B1 not independently green (`deploy/README.md` settings-group test); squash-merge can drop the trailer |

### Disposition of contested points

- **`RawDoc.metadata` timing.** Architect: defer to C (smallest surface). RAG
  engineer: need audit metadata now. **Resolved:** add the field in B3b but
  populate only minimal audit keys (`parser`, `parse_status`, `chunker`,
  `chunk_words`) and **only on the parsed path**, so text-path chunks stay
  byte-identical. Page/heading metadata waits for C.
- **`DocumentParseError`.** Architect: marginal, protected. Counter-argument
  that wins: `ConfigError` (400) and `PersistenceError` mislabel an upstream
  parser failure, and operators need to tell "parser down" from "bad config".
  **Resolved:** keep it, isolated in a single trailer-bearing commit; mapped to
  502 because the `tests/test_errors.py` status walk requires every error be
  mapped even though v1 is CLI-only. ADR records the reasoning.
- **HTML in default `allowed_suffixes`.** Security: remove (CVE history in
  Docling's HTML backend). **Accepted:** default is `.pdf .docx .pptx .xlsx`.
- **In-process provider (D1).** Security: forfeits isolation. UX: Windows
  local-first users lack Docker. **Resolved:** keep D1, but explicit opt-in,
  CLI-only, and a subprocess with resource limits for untrusted input.
- **Sample size.** Eval scientist wants 60–100 questions / ≥15 docs; labelling
  cost is real. **Resolved:** PR A2 is budgeted for it, and the decision rule
  has a first-class "inconclusive" outcome so an under-powered result is never
  recorded as a negative.
- **Does the API service need parser env at all?** Ingest is a CLI/operator
  action, so `deploy/service.yaml` likely needs **no** `MANGOMAS_PARSER__*`
  entries (CI reviewer assumed it does). To be confirmed in A1; the README
  settings-group test still requires the group be *documented*.

### Errors in rev 1 that this revision corrects

References to a nonexistent "M5"; a "✅-when-merged" marker on a Draft plan;
treating the bake-off as a unit test; tag-pinning an image meant to be
digest-pinned; assuming `docling-serve` auth, URL-fetch and container-user
behaviour without verification.

## Verified vs unverified

**Verified in this repo** (read or grepped): the chunker flattening; pipeline
embed-before-delete and empty-doc purge (`pipeline.py:146-176,246`); `rglob`
following symlinks (`loader.py:59`); single-file `source` is `root.as_posix()`
as typed (`loader.py:71`); the CLI builds `IngestionPipeline` directly
(`cli/commands/rag.py:59`); `ENV_GATE_SUITES`/`HOSTED_RUNNER_INFEASIBLE` in
`tests/constants/live.py`; marker-based gating in `tests/conftest.py`;
`deploy/` contains only `service.yaml` + README; `pyproject.toml` and
`errors.py` are trailer-protected; next free numbers are spec-0035 / ADR-0036.

**Web-sourced, treat as claims until A1 pins them:** docling 2.134.0, MIT;
docling-serve endpoints, response `status` values, `X-Api-Key` auth,
`document_timeout`; the ~4.4 GB CPU image and 4 vCPU / 8–16 GB sizing; four 2026
Docling CVEs fixed by 2.91/2.94 (**reviewers cited different CVE IDs; the IDs
are unreconciled — pin the version floor from the advisory itself, not from this
document**).

**Unverified, A1 must settle before B2:** serve's default auth (assume open);
whether `/v1/convert/source` can be disabled; container user; whether `md_content`
excludes headers/footers; HybridChunker tokenizer/metadata/`contextualize()`
behaviour (docs site blocked by the egress proxy); `docling-core[chunking]`
install weight and offline tokenizer story; converter thread-safety; model-weight
download/egress needs.

## PR A1 — Decisions and verification spike (docs + recorded fixtures only)

### Milestone A1.1 — Verify the unverified

- **Failing test first:** none (research). Deliverable is a recorded fixture:
  real `docling-serve` request/response pairs (success, `partial_success`,
  `failure`, `skipped`, 401, oversize) committed under `tests/fixtures/` so B2
  tests are pinned to the actual contract, not memory.
- **Depends on:** nothing — parallel-safe.
- Answer every item in "Unverified" above; record the pinned image **digest**,
  docling version, layout/table model revisions and OCR preset.

### Milestone A1.2 — Spec-0035 and ADR-0036 (use `mango-adr-author`)

- **Failing test first:** none — but ADR must be **Accepted** before PR B1 merges
  (merge-order rule, enforced in review).
- ADR must record decisions, not aspirations:
  1. **Service auth:** IAM ID-token (`roles/run.invoker`, `--no-allow-unauthenticated`,
     internal ingress, dedicated SA) primary; `X-Api-Key` defence-in-depth only;
     only `/v1/convert/file` reachable (front with proxy or config flag, per A1.1).
  2. **Operator privilege:** "ingest is an operator action; the corpus is global
     and not tenant-isolated; no upload endpoint until a tenancy ADR supersedes
     this" (ADR-0033 honesty). Consider reserving a per-source `tenant` metadata
     key now so a later fix needs no re-ingest.
  3. **Empty parsed text:** a non-empty file whose parse returns empty text is a
     **parse failure** (skip/fail per `on_error`), never a purge. A genuinely
     empty `.md`/`.txt` keeps today's purge behaviour.
  4. **`on_error`** semantics: `skip` (default; logged, counted, never purges)
     vs `fail`; with the per-document iterator, `fail` on file N leaves files
     1..N-1 already replaced (stated, not hidden).
  5. **Data residency:** documents leave the host to serve — same region, body
     logging disabled, retained in memory only (verify), PII note.
  6. **Untrusted content:** parsed text is data; hidden text (white-on-white,
     tiny/off-page) reaches the index; control is output framing + a poisoned-PDF
     test (below), not a promise.
  7. **`DocumentParseError`** and its 502 mapping (see dispositions).
  8. **Inherited debt:** the CLI builds `IngestionPipeline` directly; the parser
     is constructed in `composition/parser.py` and read off the context like
     embeddings. A `build_ingestion_pipeline` composition factory is a separate
     follow-up.
  9. **Rollback:** set `MANGOMAS_PARSER__ENABLED=false`. Already-ingested
     vectors remain (no migration); turning the parser off does **not** purge
     PDF-derived vectors — documented, with a manual `delete_by_source` recipe.
- Spec requirements to state explicitly: every default/limit value; `documents`
  in `IngestReport` and spans means "successfully yielded", skipped counted
  separately; the nonexistent-path `ConfigError` stays raised inside the
  `rag.ingest` span before the first yield.

## PR A2 — Gated measurement harness (gate wiring + corpus + metrics)

Redesigned from rev 1. It decides PR C, so it is built as an instrument, not a
smoke test.

### Milestone A2.1 — Gate wiring (do first; otherwise `make gate` fails)

- **Failing test first:** `tests/deploy/test_gated_suite_homes.py` goes red when
  `RUN_DOCLING` is introduced unregistered; plus a test that the bake-off
  **skips** when the flag is unset (default suite stays green).
- Register `RUN_DOCLING` in `tests/constants/live.py` — in
  `HOSTED_RUNNER_INFEASIBLE` (needs a live serve + real embedder) with a reason,
  and make sure no workflow invokes it; add a `docling` pytest marker and its
  `RUN_DOCLING` branch in `tests/conftest.py` (marker-based, like `rag`); add a
  `Makefile` target (in `.PHONY`) and update `tests/deploy/test_ci_make_parity.py`
  expectations if it enumerates targets.

### Milestone A2.2 — Corpus and labels

- **Corpus:** ≥15 documents, redistributable only (CC-BY/public-domain/synthetic);
  hosted outside the repo and fetched by pinned hash, or generated. Stratified:
  table-heavy PDF, prose PDF, scanned PDF, DOCX/PPTX/XLSX; declare English-only
  scope or add a small non-English slice. Include one **poisoned PDF** (hidden
  instruction text).
- **Labels:** 60–100 questions, **chunker-agnostic gold evidence spans**
  (verbatim passage), not "source + heading" (heading labels reward the heading
  chunker by construction). Cap questions per document; ≥30% target tables;
  two-person labelling on a 20-question subsample with agreement reported.
  Dev/test split: tune on dev, score a frozen test slice **once**.
- **Frozen parse artifacts:** commit/hash the parsed markdown so chunker
  comparisons are decoupled from parser nondeterminism.

### Milestone A2.3 — Metrics, arms, decision rule

- **Primary:** recall@5 on evidence spans (text-overlap match against retrieved
  chunks). **Secondary:** MRR@10, nDCG@10, context precision; per-stratum
  numbers; **parse-recall** (is the gold span present in parsed text at all —
  separates OCR/parse misses from retrieval misses); chunk-count and token-length
  distribution; fraction of chunks over the embedder's context limit; parse
  latency p95 per page, peak memory, cost per 100 pages. Small downstream check:
  answer faithfulness via the existing `llm_judge` with a fixed answerer/judge.
- **Arms:** parsers {pypdfium backend, Docling default, optionally pymupdf4llm}
  × chunkers {word-window, line-preserving window, markdown-heading,
  HybridChunker when C3 exists}; **equal token budget across arms**; sweep ≥3
  chunk sizes for baseline and winner (size sensitivity often exceeds structure
  effect in published comparisons — verify citations in the spec).
- **Statistics:** paired **cluster bootstrap resampled by document**; report
  deltas with 95% CI. At ~80 questions expect to resolve only large effects
  (roughly ≥10–15 points pooled); the table stratum is where a large effect is
  hypothesised, so powering is aimed there.
- **Decision rule (pre-registered in spec-0035 — one statistic, fixed
  thresholds):** adopt a chunker/parser variant only if **all** hold on the
  frozen test slice: (a) the lower bound of the 95 % document-cluster bootstrap
  CI of Δrecall@5 (variant − baseline) is **> 0**; (b) no stratum's point-estimate
  Δrecall@5 is below **−5 points**; (c) parse latency p95 is **≤ 10 s per page**
  and total ingest wall time is **≤ 3×** the baseline arm on the same corpus and
  hardware. If the CI contains zero → **"inconclusive; needs more data"**, never
  "negative"; if its upper bound is < 0 → reject. There is no alternative
  criterion. The gate in CI is regression-only on the frozen test slice.
- **Reproducibility pins** stored in report metadata: serve image digest, docling
  version, model revisions, OCR preset, embedding model id + revision, chunker
  parameters.

## PR B1 — Protocol, settings, fake (smallest possible; no behaviour)

### Milestone B1.1 — Seam, settings, docs

- **Failing test first:** `tests/adapters/parsers/test_base.py`
  (`isinstance(FakeDocumentParser(), DocumentParser)`); facade-identity tests in
  `tests/test_import_compat.py` for `config.ParserSettings` and
  `adapters.parsers`; `tests/test_config.py` defaults + validators in **both
  directions**; `test_env_example_contract` red until `.env.example` + `AGENTS.md`
  table document every var **including the list/enum defaults** (`allowed_suffixes`,
  `on_error` — the contract only compares scalar defaults, so add explicit checks);
  `test_readme_documents_every_settings_group` red until `deploy/README.md`
  documents the `PARSER` group; `tests/tooling/test_directory_claude_md.py` red
  until `adapters/CLAUDE.md` and `composition/CLAUDE.md` tables list the new seam.
- **Depends on:** PR A1 merged and ADR-0036 Accepted.
- `adapters/parsers/base.py`: `@runtime_checkable DocumentParser`
  (`async parse(*, filename, content: bytes) -> ParsedDocument`, `aclose`);
  `ParsedDocument(text, pages: int | None = None, partial: bool = False)` —
  primitives only (mirror `VectorMatch`); no free-form `metadata` yet.
- `config/parser.py` `ParserSettings` (all `DEFAULT_*` constants, re-exported via
  `config/__init__.py`, added to `_root.py`): `enabled=False`, `provider`,
  `base_url`, `api_key`, `secret_ref` (same precedence as
  `MANGOMAS_LLM__SECRET_REF`), `timeout_seconds`, `document_timeout_seconds`
  (validator: outer timeout **>** document timeout, tested both ways),
  `allowed_suffixes` (default `.pdf .docx .pptx .xlsx`), `max_file_bytes`,
  `max_pages`, `max_response_bytes`, `max_zip_entries`, `max_zip_ratio`,
  `on_error`, `ocr_preset`, `parsed_chunk_words`, `embed_max_tokens` (optional).
- `tests/fakes.py` `FakeDocumentParser`: records calls, scriptable text/raise,
  latency via `asyncio.Event` (no wall-clock sleeps). URLs, fixture payloads, the
  sentinel key and filenames go in `tests/constants` (re-export mirrored
  defaults `X as X`, never restate).
- Expected coverage floors stated up front: `adapters/parsers` → adapters 85 %;
  `rag` 95 %; `config`/`composition` count toward global 95 % only.
- CHANGELOG `[Unreleased]` entry (every PR updates it).

### Milestone B1.2 — `DocumentParseError` (isolated commit; protected path)

- **Failing test first:** `tests/test_errors.py` status walk fails until mapped;
  assert `.code == "document_parse_error"`, HTTP 502, base class, and `detail`
  truncation limit.
- **Depends on:** B1.1. Keep this to **one commit** touching `errors.py` and
  `api/errors.py::_ERROR_STATUS` so the trailer-bearing commit is the only
  protected-path change. Trailer:
  `BREAKING-CHANGE: src/mangomas/errors.py — add DocumentParseError`.
- **Merge rule:** the PR must **not** be squash-merged unless the squash message
  carries the trailer (trailers are read from commit messages;
  `make protected-paths` needs `BASE_REF` set and the trailers present).

## PR B2 — `docling_serve` adapter

### Milestone B2.1 — Client, limits, auth, tests

- **Failing test first** (`respx`, using A1.1's recorded fixtures):
  success → text; `partial_success` → `ParsedDocument.partial=True` + warning +
  report accounting (never silently indexed as clean); `failure`, `skipped`,
  **unknown status value** → `DocumentParseError`; empty `md_content` →
  empty text (policy applied upstream); 401/403 → config-class error (not
  transient); 5xx/timeouts/connect errors → typed (no raw `httpx`); malformed
  JSON → typed with truncated `detail`; response larger than
  `max_response_bytes` refused; oversize file (boundary `n` sent, `n+1`
  refused; `max_file_bytes` is strictly positive — no "0 = off" mode, `0` is
  rejected by the settings validator) refused **before any network call**
  (`respx` route call count 0; size via `stat`, not read-then-check).
- **Request hygiene (asserted on the request body):** `from_formats` pinned to
  the file's allowed format; **no** `ocr_engine` (use `ocr_preset`); sanitised or
  generated multipart filename (Unicode-safe), never the original path; only
  `/v1/convert/file` is ever called; `max_num_pages`/`document_timeout` sent.
- **Secrets:** sentinel-key test with `caplog` + `str()`/`repr()` of exceptions
  and spans; `X-Api-Key` header present only when a key is set (two-sided);
  `secret_ref` over `api_key` precedence. Log fields are an allow-list: a
  canary string in a document or serve error body must **not** appear in logs or
  span attributes.
- **Depends on:** B1.1 (parallel-safe with B1.2 until the error is needed).
- `composition/parser.py` + `parsers` registry entry; constructs nothing unless
  `enabled`; flag-off test asserts no parser built and no `httpx` client opened.
- Lazy-import pragma only where an SDK import exists (none here); fixtures live
  in `tests/`, helper reuse from `_http_errors.py` where it fits — avoid
  subclassing the LLM-shaped `OpenAICompatHTTPClient` blindly.

## PR B3a — Loader (`iter_documents`) with containment

### Milestone B3a.1 — Iterator, symlink and source canonicalisation

- **Failing test first:** (1) **parity**: `iter_documents` yields exactly what
  `load_documents` returns for text trees — Hypothesis over file trees (empty
  file, unicode, nested dirs, single-file path), plus order independent of
  directory creation order; (2) **symlink escape**: a symlink inside the root
  pointing at a file outside it is skipped (and logged), proven red first;
  (3) case-insensitive suffix (`A.PDF`); (4) Windows-style paths yield POSIX
  `source` keys so re-ingest replaces rather than duplicates; (5) single-file
  `source` canonicalised for the **parsed path** (`./a.pdf` ≡ `a.pdf`;
  never an absolute path leaked into retrievable metadata) while the text path
  keeps `root.as_posix()` unchanged (byte-identical).
- **Depends on:** B1.1.
- **Do not change `_load_documents_sync`** (pinned by `tests/rag/test_loader.py`
  and `tests/regression/test_sdlc_gate_defects.py:57-74`); `iter_documents` is a
  new async generator reusing its helpers. `load_documents` signature is
  permanent.
- Pre-upload checks: `stat` size; for `.docx/.pptx/.xlsx` zip entry-count and
  compression-ratio ceilings (zip-bomb guard); symlink containment via
  `resolve()` + `is_relative_to(root.resolve())`.

## PR B3b — Pipeline + CLI integration (default-OFF)

### Milestone B3b.1 — Never-purge, line-preserving chunking, accounting

- **Failing test first** (each one tests a distinct behaviour; do not bundle):
  1. parse failure of `a.pdf` with `on_error=skip` leaves `a.pdf`'s existing
     vectors **and count** untouched (`FakeVectorStore` pre-seeded);
  2. **opposite direction:** an empty `.md` still purges (today's behaviour);
  3. parser returns empty text for a non-empty file → treated as a parse
     failure, no purge (ADR-0036 §3);
  4. `on_error=fail` raises, and files 1..N-1 are persisted;
  5. `partial_success` document is indexed with `parse_status=partial` and
     counted;
  6. a parsed markdown **table keeps its newlines and separator row** in the
     stored chunk (this is the blocker-1 regression test);
  7. all documents failing to parse → `skipped`, **not** `rag_ingest_empty`;
  8. flag-off golden: a `.txt/.md/.pdf` tree with `enabled=false` yields the
     same `IngestReport`, chunk ids, metadata and CLI string as today, and no
     parser is constructed;
  9. `aclose` called once in the CLI `finally`, including when ingest raises;
  10. `RetrievalTool` output byte-identical when no chunk is parser-derived;
      parser-derived chunks are returned inside an explicit untrusted-data
      delimiter, and the poisoned-PDF case does not make the agent follow the
      injected instruction.
- **Depends on:** B2.1, B3a.1.
- `IngestionPipeline.__init__(..., parser: DocumentParser | None = None)` —
  additive keyword; existing constructors in `tests/rag/test_pipeline.py` stay
  valid. `IngestReport.skipped_documents: int = 0` (default-safe; pinned
  equality tests keep passing).
- **Chunking for parsed docs only:** a whitespace/line-preserving fallback window
  in `rag/chunker.py` (new function; `chunk_text` untouched so text-path output
  is byte-identical), sized by `parsed_chunk_words` (provisional default chosen
  conservatively for 512-token embedders; A2 replaces it with a measured value)
  and a `rag_chunk_over_budget` warning when estimated tokens exceed
  `embed_max_tokens`.
- **Metadata (parsed path only):** `RawDoc.metadata` (immutable/`default_factory`,
  tested against shared-mutable-default); minimal audit keys `parser`,
  `parse_status`, `chunker`, `chunk_words`, `embedding_model`; keys namespaced,
  `source`/`index` **always win** (build `{**doc_meta, "source": …, "index": …}`),
  `None`/list/dict values dropped or flattened (Chroma rejects `None`; Hypothesis
  property over nested/non-string-key inputs); heterogeneous-key batches accepted
  by the (fake) store; re-ingesting a source previously `.txt` and now parsed
  replaces cleanly.
- `rag/pipeline.py`: consume the iterator; `rag.documents` / "started" log use a
  running "yielded" count; span `rag.parse` per document with pages/bytes only;
  event `rag_document_parse_failed` (source + error code, no content).
- `cli/commands/rag.py`: help text kept byte-compatible (pinned by
  `tests/test_cli_rag.py` and `tests/constants/cli.py:26,64`, or constants
  updated deliberately); `skipped=` printed only when non-zero.
- Known hazard recorded in docs, not fixed here: changing the embedding model
  makes the store raise a dimension mismatch **after** the source is deleted.
  A fail-before-delete guard needs a store-side capability protocol
  (`VectorStoreRepository` cannot grow a method) — separate spec; PDFs make it
  costlier, so it is on the follow-up list with priority.

## PR B4 — Operations (separate service; not required for local use)

### Milestone B4.1 — Deploy docling-serve safely

- **Failing test first:** new `tests/deploy/` contract(s): serve manifest exists
  and is **digest-pinned** (and minimum-version regex encodes the CVE floor);
  unauthenticated invocation not allowed; ingress internal; no literal
  secrets/URLs (`secretKeyRef` for any `*_API_KEY` / `*__URL`); non-root and
  resource limits asserted; Dependabot entry for the image.
- **Depends on:** A1.1 (verified auth/user facts), B2.1.
- **Do not reuse** `deploy/service.yaml` / the `deploy.yml` `sed` image rewrite /
  `/healthz` smoke path (they assume exactly one image and a first-party
  health contract). Add `deploy/docling-serve.yaml`, its own deploy job (written
  contract-tests-first, `test_workflow_hardening` applies), a health probe that
  fits serve, an instance cap, low concurrency, and a memory limit sized per the
  4 vCPU / 8–16 GB guidance (verify).
- Hardening: IAM ID-token invoker auth; egress deny-all except what is needed;
  **models baked into the image** (no runtime Hugging Face download) with a pinned
  revision; image vulnerability scan in CI (the `pip-audit` equivalent for this
  path); same region as the data; body logging off.
- `deploy/service.yaml` is **not** edited unless A1.2 shows the API service
  itself ingests; `deploy/README.md` documents the `PARSER` group regardless.
- Observability/SLO: metrics `parse_failures`, `skipped_documents`, p95 parse
  latency; alert on skipped-document ratio; per-document cost and cold-start
  budget (4.4 GB image, `minScale: 0`) recorded in the ADR. Docs page,
  `mango-rag` skill update (run `make frontmatter`), local-dev recipe
  (`docker run` the CPU image; Windows/Docker caveat).

## PR C — Structure-aware chunking (**only if PR A2's rule is satisfied**)

### Milestone C1 — Line-preserving markdown splitter with header repeat

- **Failing test first:** Hypothesis property over generated markdown tables:
  no row is ever split; every split chunk of a table repeats the header row;
  oversize non-table sections fall back to the window with the same overlap
  rules; heading path emitted as namespaced `doc_heading_path`.
- **Depends on:** A2 decision gate, B3b.1. Dependency-free, so `rag/` stays pure
  and the import contracts hold. Page numbers unavailable from markdown (stated).
  Merged cells / multi-row headers flatten unpredictably in Docling markdown
  (stated in the spec). Run the A2 harness with this arm before adopting.

### Milestone C2 — Contextualised embedding (embed ≠ store)

- **Failing test first:** `_PreparedBatch` carries separate embed text; stored
  text is raw, embedded text is heading-prefixed; ids/metadata unchanged.
- **Depends on:** C1 and a measured gain. Zero-code alternative (prepend heading
  path to stored text) is evaluated first. Note asymmetric-prefix embedders need
  query/document prefixes.

### Milestone C3 — Docling HybridChunker spike (only if C1 leaves a measured gap)

- **Failing test first:** gated test on a table-heavy PDF asserting page-number
  metadata and header-repeated table chunks.
- **Depends on:** C1 result; verified docling-core weight and tokenizer/offline
  story (A1.1). Lives behind an additive capability protocol in
  `adapters/parsers/` (not in `rag/`). Tokenizer must match the **embedding**
  model; LM Studio has no guaranteed HF tokenizer, so either document an
  approximate `max_tokens` or pack blocks by words. `pyproject.toml` edit needs
  `BREAKING-CHANGE: pyproject.toml — add docling-core chunking extra` in its own
  commit; keep it out of `dev` extras (lockfile freshness, `make pip-audit`).

## PR D — In-process provider (opt-in; CLI-only)

### Milestone D1 — `docling_local`

- **Failing test first:** missing-extra error path via
  `monkeypatch.setitem(sys.modules, "docling", None)` (no mock of internal
  protocols); semaphore and `to_thread` paths exercised through an injected
  converter; lazy-import helper alone carries `# pragma: no cover - requires
  docling`.
- **Depends on:** B3b.1. Parallel-safe with C.
- One converter built once; calls serialised behind a semaphore (thread-safety
  unverified); `allowed_formats` restricted and `from_formats` mirrored; `pypdfium`
  backend option; OCR off by default (EasyOCR ≈30 s/page CPU). **Isolation:**
  runs inside the invoking process with its secrets and filesystem, so it is
  explicit opt-in, CLI-only, and run in a resource-limited subprocess for
  untrusted input. Extra `docling = ["docling==<exact pin>"]` (`pyproject.toml`
  trailer in its own commit), kept out of `dev`, covered by the `pip-audit` gate;
  clean-venv resolver check against `chromadb`/`sentence-transformers` — on
  conflict, document "use `docling_serve`" and stop.

## Deferred / out of scope

- **Parse-document agent tool / `POST /documents`:** blocked on tenancy (ADR-0036
  §2), SSRF/arbitrary-read, and prompt injection. Reopen only by an ADR
  superseding §2.
- **Content-hash skip on re-ingest** and the **fail-before-delete embedding-model
  guard:** both need a store capability protocol; separate spec, prioritised
  because PDFs make re-ingest expensive.
- **Retrieval dedupe/MMR** (repeated boilerplate filling top-k) and
  **page-furniture/OCR-noise filtering:** measured in A2 first; act on evidence.
- **Retrieval-quality eval target** (`eval` may not import `rag`; would go via
  `ToolRegistry`): follow-up; A2 is the interim instrument.
- **docling-serve async endpoints**, **audio/video/email/LaTeX/XBRL/METS/HTML
  formats**, **VLM (Granite-Docling) pipeline**, **MCP server in `.mcp.json`**
  (governance-surface edit for little benefit).

## Verification

```bash
make gate                                    # full pre-PR chain, CI order
python -m pytest tests/adapters/parsers tests/rag tests/deploy -q
make lint-imports                            # rag/eval/workflow/cognitive independence
BASE_REF=origin/feat/initial-release make protected-paths   # trailers on the right commits
make frontmatter                             # after mango-rag skill edits
RUN_DOCLING=1 make docling-bakeoff           # operator-run; hosted-runner infeasible by design
```

**Mutation proofs** (`mango-mutation-proof`; record the command and diff for
each; two-sided where a guard has two directions):

1. Remove the no-purge-on-parse-failure branch → B3b test 1 red; *and* make
   "never purge empty" universal → B3b test 2 (empty `.md` purges) red.
2. Remove the `allowed_suffixes` check → red; allowed suffix still passes.
3. Let doc metadata override `source` **and** `index` → red (mutate both).
4. Remove the `max_file_bytes` pre-check → "no network call" test red.
5. Always/never send `X-Api-Key` → the two-sided header test red.
6. Invert `on_error` skip/fail; drop `partial_success` handling → red.
7. Construct the parser even when disabled → flag-off test red.
8. Remove symlink containment → escape test red.
9. Treat empty parsed text as success → B3b test 3 red.
10. Swap the line-preserving window back to `chunk_text` for parsed docs →
    table-newline test red.
