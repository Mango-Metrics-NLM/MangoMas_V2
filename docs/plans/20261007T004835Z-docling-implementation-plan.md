# Docling ingestion — implementation plan (execution runbook)

- **Branch:** one branch per PR, cut from `feat/initial-release` (names below)
- **Date:** 2026-10-07
- **Target release:** rolling (additive, default-OFF)
- **Status:** Approved — ready to execute, starting at PR 1 (A1)
- **Specs:** spec-0035 (written in PR 1)
- **ADRs:** ADR-0036 (written in PR 1; Accepted before PR 3 merges)
- **Companion:** [`20261006T233919Z-docling-document-ingestion-plan.md`](20261006T233919Z-docling-document-ingestion-plan.md)
  holds the *why* (review board, dispositions, threat model). This file is the
  *how*: PR → commit → file → test, in execution order.

## Executive summary

Nine PRs on one critical path (1 → 3 → 4 → 5 → 6 → 7), with the measurement
harness (2), the deploy work (8) and the opt-in in-process provider (9) running
beside it; structure-aware chunking (10) runs only if PR 2's pre-registered rule
says so. Each PR is independently green under `make gate`, starts with a failing
test, and ends with its mutation proofs recorded. Protected-path edits are
isolated into single trailer-bearing commits. The one constraint governing every
PR: with `MANGOMAS_PARSER__ENABLED=false`, `.txt`/`.md` ingest is byte-identical
to today.

## Decisions approved (2026-10-07)

| # | Decision | Effect on this plan |
|---|---|---|
| D1 | Label budget: 60–100 questions on redistributable documents | PR 2 is in scope as specified |
| D2 | `on_error=skip` default | Settings default; skipped docs never purge |
| D3 | New `DocumentParseError` (HTTP 502) | Isolated protected-path commit in PR 4 |
| D4 | Start with A1 (verification spike + spec + ADR) | PR 1 is first |

## Facts discovered while writing this runbook (corrections to the delivery plan)

These were verified in the repo today and change concrete steps:

1. **`pyproject.toml` is touched in PR 2, not first in C3/D1.** `--strict-markers`
   is in `addopts` and markers are registered in `[tool.pytest.ini_options]`
   (`pyproject.toml:327`), so a new `docling` marker means a governance-surface
   edit. Resolution: register it there, in its own commit, with
   `BREAKING-CHANGE: pyproject.toml — register the docling pytest marker`.
   (Rejected alternative: `config.addinivalue_line` in `conftest.py` — avoids the
   trailer but breaks the repo's single-registry convention.)
2. **Facade tables must list new submodules.** `tests/test_import_compat.py`
   `_FACADES["mangomas.config"]` and `_FACADES["mangomas.composition"]` enumerate
   submodules; `config/parser.py` and `composition/parser.py` must be added or
   `test_every_owned_public_name_reaches_the_facade` fails.
3. **Gate registration has three coupled tables.** `ENV_GATE_SUITES` and
   `HOSTED_RUNNER_INFEASIBLE` (`tests/constants/live.py:117,211`), the conftest
   collection gate (`tests/conftest.py`), and a Makefile recipe line matching
   `RUN_DOCLING=1 $(PYTHON) -m pytest …` (parsed by
   `tests/deploy/test_gated_suite_homes.py::_GATE_VAR_RE`). The zero-skip session
   guard fails any skip whose reason is not derived from `ENV_GATE_SUITES`.
4. **`specs/README.md` has an index table** — spec-0035 needs a row.
5. **`deploy/README.md` must mention every settings-group prefix**
   (`tests/deploy/test_deploy_contract.py:91`) — `MANGOMAS_PARSER__` lands in
   PR 3, not PR 8.
6. **The error walk** (`tests/test_errors.py:301,323`) requires every
   `MangomasError` subclass to have an intended status; `test_error_codes`
   (`:180`) is parametrised over code strings.
7. **CLI pins:** `tests/constants/cli.py` pins `"rag ingest": ("--verbose", "<path>")`
   and `tests/test_cli_rag.py:56` asserts `"ingested docs=1"`. No new CLI option
   is planned, so both stay untouched.
8. **Docker:** the CLI exists in the cloud session but no daemon runs. PR 1's live
   verification (task 1.1) runs on a Docker-capable machine, or after starting
   `dockerd` in-session if permitted.

## Conventions for every PR

- **Branch** from latest `feat/initial-release`; PR is opened as **draft** using
  `.github/PULL_REQUEST_TEMPLATE.md` headings; base `feat/initial-release`.
- **Order inside a PR:** failing test commit (or test + impl in one commit when a
  red-only commit would break `make gate` mid-branch — the red state is then
  shown in the PR description with the command and output) → implementation →
  docs/CHANGELOG → mutation proofs recorded in the PR body.
- **Trailer commits:** one per protected path, path bare and first:
  `BREAKING-CHANGE: <path> — <rationale>`. PRs containing one are **merged with a
  merge commit, never squashed** (trailers are read from commit messages).
- **Pre-push checklist:** `make gate`; `BASE_REF=origin/feat/initial-release make
  protected-paths`; `make lint-imports`; targeted `pytest` for the touched dirs.
- **Owners:** each PR names the specialist agent and skill; the router
  (`mango-architect`, `mango-test-engineer`) reviews.
- **Size key:** S ≤ ½ day, M ≈ 1–2 days, L ≈ 3–5 days (one engineer, excl. review).

## Critical path

```
PR1 (A1 spike+spec+ADR) ──► PR3 (B1 seam+settings) ──► PR4 (B1.2 error) ──► PR5 (B2 serve adapter) ──► PR6 (B3a loader) ──► PR7 (B3b pipeline+CLI)
   │                                                                                     │
   ├──► PR2 (A2 harness + corpus + labels) ───────────────────────────────────► PR10 (C, gated on PR2's rule)
   └──► PR8 (B4 deploy serve) ◄── PR5                                                    PR9 (D1 in-process) ◄── PR7
```

PR 6 can start in parallel with PR 5 (it depends only on PR 3).

---

## PR 1 — A1: verification spike, spec-0035, ADR-0036 · `docs/docling-a1-spec-adr` · M

Owner: `mango-adr-author` (ADR), author (spec); review `mango-architect`.

**Task 1.1 — Verify serve behaviour** (needs Docker host)
- Pull the CPU serve image; record its **digest**, the docling version it bundles,
  layout/table model revisions, default OCR preset.
- Answer, with evidence (command + output pasted into the spec appendix):
  default auth on/off; whether `/v1/convert/source` can be disabled; container
  user (root?); whether `md_content` drops page headers/footers; response fields
  and `status` values; error shape for oversize/timeout/auth failure;
  `max_num_pages` / `max_file_size` parameter names; model downloads at runtime
  (egress needed?).
- Reconcile the CVE IDs against the GitHub advisories for `docling` and set the
  **minimum version** from the advisory text.
- Save sanitised request/response pairs as fixtures:
  `tests/fixtures/docling_serve/{success,partial_success,failure,skipped,unauthorized,oversize}.json`
  plus `README.md` (provenance: image digest, date, command). Small synthetic
  inputs only.
- Also check (cheap, `pip download` in a scratch venv): `docling-core[chunking]`
  install weight; whether HybridChunker can run with a local tokenizer offline.

**Task 1.2 — `specs/0035-docling-document-ingestion.md`** (from `specs/TEMPLATE.md`)
- Requirements and WHEN/THEN scenarios transcribed from the delivery plan §B–D,
  both directions for every guard.
- Config table (exact names/defaults — see PR 3), protocol impact, error type,
  back-compat statement, test plan, acceptance criteria.
- Pre-registered decision rule for PR 10 (copied verbatim from delivery plan
  §A2.3) — fixed **before** any measurement.
- Add the row to `specs/README.md` § Index.

**Task 1.3 — `docs/adr/0036-document-parser-seam.md`** (from `docs/adr/_template.md`)
- Status **Proposed** in this PR; flipped to **Accepted** by a one-line follow-up
  commit once reviewers sign off (that commit is the gate for merging PR 3).
- Records the nine decisions in delivery plan §A1.2 plus D2/D3 above, and the
  facts from task 1.1 (auth model, serve version floor, residency).

**Done when:** spec + ADR merged; fixtures committed; every "Unverified" item in
the delivery plan has an answer or an explicit "still unknown → mitigated by X".

---

## PR 2 — A2: measurement harness · `test/docling-bakeoff-harness` · L

Owner: `mango-integration-runner` (gate wiring), `mango-rag-dev` (harness);
review `mango-test-engineer`. Depends on PR 1 (decision rule) — harness code can
start in parallel; the **labels** must not be read by whoever tunes chunkers.

**Commit 2a — register the marker** (protected)
- `pyproject.toml` `[tool.pytest.ini_options].markers`: add
  `"docling: tests that require a running docling-serve and a real embedder"`.
- Trailer: `BREAKING-CHANGE: pyproject.toml — register the docling pytest marker`.

**Commit 2b — gate wiring**
- `tests/constants/live.py`: `ENV_GATE_SUITES["RUN_DOCLING"] = "docling-serve bake-off tests"`;
  `HOSTED_RUNNER_INFEASIBLE["RUN_DOCLING"] = "needs a running docling-serve
  container plus a real embedding backend; hosted runners provision neither"`.
- `tests/conftest.py` `pytest_collection_modifyitems`: add
  `if "docling" in item.keywords and not enabled["RUN_DOCLING"]: item.add_marker(skip["RUN_DOCLING"])`.
- `Makefile`: add `docling-bakeoff` to `.PHONY`; target
  `docling-bakeoff: ## Docling bake-off (needs docling-serve + embedder; RUN_DOCLING=1)`
  with recipe `RUN_DOCLING=1 $(PYTHON) -m pytest tests/rag/bakeoff --no-cov $(PYTEST_FLAGS)`.
- Tests: `tests/tooling/test_collection_gate.py` (or the existing subprocess
  test) proves a `@pytest.mark.docling` test **skips** without the flag and the
  zero-skip guard sanctions it; `make gate` stays green.

**Commit 2c — harness code** (`tests/rag/bakeoff/`, test-only code)
- `corpus.py`: manifest of documents `{id, url, sha256, licence, stratum}`;
  fetch-and-verify into a cache dir outside the repo (`$MANGOMAS_BAKEOFF_CACHE`,
  default under the user cache dir); refuses a hash mismatch.
- `labels.jsonl` schema: `{qid, doc_id, question, evidence_spans: [str], stratum,
  split: dev|test}`.
- `metrics.py` (pure, unit-tested in the **default** suite, no marker):
  span-overlap match, recall@k, MRR@k, nDCG@k, context precision, parse-recall,
  cluster bootstrap by document → `(delta, ci_low, ci_high)`.
- `test_bakeoff.py` (`@pytest.mark.docling`): parse corpus via the real serve
  (frozen-artifact mode reads committed hashes of parsed markdown), chunk with
  each arm, ingest into a temp Chroma via the real embedder, score, write a JSON
  report with the reproducibility pins, and assert only the **regression gate**
  on the frozen test slice.
- Unit tests for `metrics.py` with hand-computed fixtures, including the
  bootstrap's determinism under a fixed seed.

**Commit 2d — corpus + labels (data, separate review)**
- ≥15 redistributable documents across strata (table PDF, prose PDF, scanned PDF,
  DOCX, PPTX, XLSX) + one poisoned PDF; 60–100 questions, ≥30 % tables,
  per-document cap, dev/test split; 20-question double-labelled subsample with
  agreement in the PR body.

**Done when:** `make gate` green with the flag unset; `make docling-bakeoff` runs
end-to-end on a Docker host and produces the **baseline** (word-window) report,
committed under `docs/eval/docling-bakeoff/baseline.json`.

---

## PR 3 — B1.1: protocol, settings, fake, docs · `feat/parser-seam` · M

Owner: `mango-rag-dev` + `mango-config` skill; review `mango-architect`,
`mango-protocol-auditor`. Depends on PR 1 merged and ADR-0036 **Accepted**.

**Files**
- `src/mangomas/adapters/parsers/__init__.py`, `base.py`:
  ```python
  @dataclass(frozen=True)
  class ParsedDocument:
      text: str
      pages: int | None = None
      partial: bool = False

  @runtime_checkable
  class DocumentParser(Protocol):
      async def parse(self, *, filename: str, content: bytes) -> ParsedDocument: ...
      async def aclose(self) -> None: ...
  ```
- `src/mangomas/config/parser.py`: `DEFAULT_PARSER_*` constants +
  `ParserSettings(BaseModel)` with fields `enabled=False`,
  `provider="docling_serve"`, `base_url`, `api_key`, `secret_ref=None`,
  `timeout_seconds`, `document_timeout_seconds`, `allowed_suffixes=(".pdf",
  ".docx", ".pptx", ".xlsx")`, `max_file_bytes`, `max_pages`,
  `max_response_bytes`, `max_zip_entries`, `max_zip_ratio`,
  `on_error: Literal["skip","fail"]="skip"`, `ocr_preset=None`,
  `parsed_chunk_words`, `embed_max_tokens=None`; `model_validator`:
  `timeout_seconds > document_timeout_seconds`, positive limits, suffixes
  lower-cased and dot-prefixed. Numeric defaults come from PR 1's findings.
- `config/__init__.py` (re-export each name `X as X`), `config/_root.py`
  (`parser: ParserSettings = Field(default_factory=ParserSettings)`).
- `tests/fakes.py`: `FakeDocumentParser` (scripted `{filename: text | Exception}`,
  `calls` list, optional `asyncio.Event` gate, `closed` flag).
- `tests/constants/`: parser env names, sentinel API key, fixture filenames;
  defaults re-exported from `mangomas.config` (`X as X`).
- Docs: `.env.example` (`MANGOMAS_PARSER__*` block), `AGENTS.md` config table
  rows, `deploy/README.md` mentions `MANGOMAS_PARSER__`, `adapters/CLAUDE.md`
  Protocol table row (`parsers` | `DocumentParser`), `CHANGELOG.md` `[Unreleased]`.
- `tests/test_import_compat.py`: add `"parser"` to `_FACADES["mangomas.config"]`.

**Tests (red first)**
- `tests/adapters/parsers/__init__.py`, `test_base.py`:
  `test_fake_satisfies_protocol`, `test_parsed_document_is_frozen`,
  `test_parsed_document_defaults`.
- `tests/test_config.py`: `test_parser_defaults_are_off`,
  `test_parser_timeout_must_exceed_document_timeout` (both directions),
  `test_parser_suffixes_normalised`, `test_parser_limits_must_be_positive`,
  `test_parser_env_overrides` (nested delimiter).
- Contract tests that go red until docs land: `test_env_example_contract`,
  `test_readme_documents_every_settings_group`, `test_directory_claude_md`,
  `test_import_compat`. Add an explicit test pinning the list/enum defaults
  (`allowed_suffixes`, `on_error`) that the scalar comparison does not cover.

**Mutation proofs:** set `enabled=True` default → `test_parser_defaults_are_off`
red; drop the timeout validator → its test red (both directions).

---

## PR 4 — B1.2: `DocumentParseError` · `feat/document-parse-error` · S

Owner: `mango-error-taxonomy-dev` + `mango-error` skill. Depends on PR 3.
**Single commit**, merge-commit only:

- `src/mangomas/errors.py`: `class DocumentParseError(MangomasError)` with
  `code = "document_parse_error"`, optional `.source: str | None`; docstring
  states "detail carries status/length only — never document content"; added
  to the module's `__all__` (every public error is listed there).
- `src/mangomas/api/errors.py::_ERROR_STATUS[DocumentParseError] = 502`.
- `tests/test_errors.py`: add to `test_error_codes` parametrisation and to the
  intended-status table; `test_document_parse_error_stores_source`.
- `AGENTS.md` § Error Types tree; `CHANGELOG.md`.
- Trailer: `BREAKING-CHANGE: src/mangomas/errors.py — add DocumentParseError (ADR-0036)`.

**Mutation proof:** remove the `_ERROR_STATUS` row → status-walk test red.

---

## PR 5 — B2: `docling_serve` adapter + composition · `feat/docling-serve-adapter` · L

Owner: `mango-rag-dev` (+ `mango-adapter` skill); review `mango-architect`,
security review via `/security-review`. Depends on PR 4 (and PR 1 fixtures).

**Files**
- `src/mangomas/adapters/parsers/docling_serve.py`: `DoclingServeParser`
  (injectable `httpx.AsyncClient`; `rstrip("/")` base URL). `parse()`:
  1. refuse `len(content) > max_file_bytes` → `DocumentParseError` before I/O
     (`max_file_bytes` is strictly positive — there is no "0 = off" mode; the
     validator rejects `0`, so the comparison never needs a guard);
  2. zip-bomb pre-check for OOXML suffixes (`zipfile` entry count + total
     uncompressed / compressed ratio) — pure function `_check_ooxml(content, …)`;
  3. POST multipart to `/v1/convert/file` with a **generated** filename
     (`document{suffix}`), `from_formats=[<mapped format>]`, `to_formats=["md"]`,
     `document_timeout`, `max_num_pages`, `do_ocr`/`ocr_preset` (never
     `ocr_engine`); authentication through an `auth_mode` strategy
     (`none` | `api_key` | `google_id_token`): `api_key` sends `X-Api-Key`;
     `google_id_token` sends `Authorization: Bearer <identity token>` for a
     private Cloud Run service (audience = `id_token_audience`, defaulting to
     `base_url`), minted by an injectable token-provider seam (default: lazy
     `google-auth` import, cached and refreshed before expiry by a configured
     margin) — the same identity-token mechanism `.github/workflows/deploy.yml`
     uses for its smoke probe;
  4. stream the body with a `max_response_bytes` cap;
  5. map `status`: `success` → `ParsedDocument(text, pages)`; `partial_success` →
     `partial=True` + `warning` log `parser_partial_success`; `failure`/`skipped`/
     unknown → `DocumentParseError`; 401/403 → `ConfigError` subclass message
     "docling-serve rejected credentials"; 5xx/timeout/connect →
     `DocumentParseError` via the shared translation in `_http_errors.py`
     (extend with a parser variant if its LLM-specific classes don't fit).
  - Logs: event, status, byte length, page count, elapsed — **allow-listed
    fields only**; no filename beyond suffix, no body text, no serve error body.
- `src/mangomas/composition/parser.py`: `_build_parser(settings, secrets) ->
  DocumentParser | None` (returns `None` when disabled, constructing nothing);
  key resolution: `secret_ref` via `SecretsProvider` overrides `api_key`.
- `composition/_registries.py`: `_parser_registry: Registry[Callable[[ParserSettings], Any]] = Registry("parser")`,
  registered `"docling_serve"`; `composition/builder.py`: attach to
  `ctx.extras[PARSER_EXTRAS_KEY]` when enabled (constant in
  `adapters/parsers/__init__.py`, mirroring the cognitive-sink pattern).
- **Teardown:** a composition-layer `_ParserCloseMixin` extends
  `Orchestrator._close_hooks` (the `_AgentLLMOverrideCloseMixin` pattern in
  `composition/llm.py`) so the parser is closed by `orch.aclose()` on the same
  fault-isolated, idempotent path as the other adapters — covering the FastAPI
  lifespan and every composed entry point, not only `rag ingest`. Tests:
  closed exactly once via `orch.aclose()`, a raising `aclose` does not stop the
  other hooks, a second `aclose()` is a no-op.
- Docs: `composition/CLAUDE.md` registry table row; `_FACADES["mangomas.composition"]` add `"parser"`.

**Tests** (`tests/adapters/parsers/test_docling_serve.py`, `respx`, fixtures from PR 1)
- Status mapping: `test_success_returns_text_and_pages`,
  `test_partial_success_sets_partial_and_warns`,
  `test_failure_and_skipped_raise`, `test_unknown_status_raises_typed`,
  `test_empty_md_content_returns_empty_text`.
- Transport: `test_5xx_timeout_connect_are_typed` (parametrised; no raw `httpx`
  escapes), `test_401_403_are_config_errors`, `test_malformed_json_truncated_detail`.
- Limits: `test_oversize_refused_without_network` (boundary `n`, `n+1`; respx
  route `call_count == 0`), `test_response_over_cap_refused`,
  `test_zip_bomb_refused_without_network`.
- Request hygiene (assert on captured request): `test_generated_filename_not_path`,
  `test_from_formats_pinned_to_suffix`, `test_no_ocr_engine_field`,
  `test_only_convert_file_endpoint_called`, `test_api_key_header_two_sided`,
  `test_id_token_bearer_header_and_refresh` (injected token provider: cached
  until the refresh margin, re-minted after; no `X-Api-Key` in this mode).
- Secrets/logging: `test_secret_never_in_logs_errors_or_spans` (sentinel key +
  canary document text + canary serve error body; `caplog`, `str/repr` of
  exceptions, in-memory span exporter).
- Composition (`tests/composition/test_parser.py`):
  `test_disabled_builds_nothing_and_opens_no_client`,
  `test_secret_ref_overrides_api_key`, `test_registry_lists_docling_serve`.

**Mutation proofs:** remove size pre-check (#4); always/never send key (#5);
drop `partial_success` branch (#6); build parser when disabled (#7).

---

## PR 6 — B3a: `iter_documents` with containment · `feat/rag-iter-documents` · M

Owner: `mango-rag-dev`; review `mango-test-engineer`. Depends on PR 3 (parallel
with PR 5 using `FakeDocumentParser`).

**Files** — `src/mangomas/rag/loader.py` (additive only; `_load_documents_sync`
and `load_documents` untouched):
```python
async def iter_documents(
    path: str, *, parser: DocumentParser | None = None,
    settings: ParserSettings | None = None,
) -> AsyncIterator[RawDoc | ParseFailure]: ...
```
- `ParseFailure(source: str, error: MangomasError)` frozen dataclass (so the
  pipeline decides skip vs fail; the loader never purges or raises per-document
  except `ConfigError` for a missing path, raised before the first yield).
- Text suffixes keep today's code path (reuse the existing helpers) — so the
  parity test is meaningful. Parsed suffixes only when `parser` is given and the
  suffix is in `allowed_suffixes` (case-insensitive).
- Containment: skip (warn `rag_symlink_escape`) any file whose `resolve()` is not
  `is_relative_to(root.resolve())`; `stat().st_size` checked before reading.
- Parsed-path single-file `source` = file name (no absolute path); directory
  sources unchanged (`relative_to(root).as_posix()`).
- Reads via `asyncio.to_thread`; one document in memory at a time.

**Tests** (`tests/rag/test_loader_iter.py`)
- `test_iter_parity_with_load_documents` — Hypothesis over generated trees
  (empty files, unicode names, nested dirs, single-file path, non-UTF-8 file).
- `test_iter_order_independent_of_creation_order` — Hypothesis.
- `test_symlink_outside_root_skipped` / `test_symlink_inside_root_followed`.
- `test_uppercase_suffix_parsed`, `test_parser_none_skips_pdf_like_today`.
- `test_parsed_single_file_source_is_not_absolute`,
  `test_windows_style_relative_source_is_posix` (`PureWindowsPath` input).
- `test_parse_error_yields_parse_failure_not_raise`,
  `test_missing_path_raises_before_first_yield`.

**Mutation proofs:** remove containment (#8); bypass `allowed_suffixes` (#2,
two-sided).

---

## PR 7 — B3b: pipeline + CLI integration · `feat/rag-parsed-ingest` · L

Owner: `mango-rag-dev` + `mango-cli-dev`; review `mango-architect`,
`mango-test-engineer`. Depends on PR 5 and PR 6.

**Files**
- `src/mangomas/rag/chunker.py`: new `chunk_lines(text, *, size, overlap) -> list[str]`
  — windows over word *spans* and returns **slices of the original string**
  (start of the window's first token, including any leading whitespace for
  the first window, to the end of its last token), so indentation, line breaks
  and table rows survive; never splits inside a markdown table row when the row
  fits the window. Regression cases: leading whitespace, indented code,
  tables. `chunk_text` unchanged.
- `src/mangomas/rag/loader.py`: `RawDoc.metadata: Mapping[str, Any] =
  field(default_factory=dict)` (frozen; `MappingProxyType` on construction).
- `src/mangomas/rag/pipeline.py`:
  - `IngestionPipeline.__init__(…, parser: DocumentParser | None = None,
    parser_settings: ParserSettings | None = None, embedding_model: str | None = None)`
    — `embedding_model` is passed explicitly from `cfg.embeddings.model` (the
    `EmbeddingClient` protocol exposes no model id), and the metadata key is
    omitted when it is `None`;
  - `ingest()` consumes `iter_documents`; running `yielded` count for the span
    attribute and logs; `rag_ingest_empty` only when zero yielded **and** zero
    skipped;
  - `ParseFailure` → `on_error="skip"`: log `rag_document_parse_failed`
    (source, error code), `skipped += 1`, **no `_replace_source`**;
    `on_error="fail"`: raise;
  - parsed doc with empty text from a non-empty file → `ParseFailure`;
  - chunk parsed docs with `chunk_lines(size=parsed_chunk_words, …)`; text docs
    with `chunk_text` exactly as today;
  - metadata: text docs unchanged (`{source, index}`); parsed docs
    `{**_flatten(doc.metadata), "parser", "parse_status", "chunker", "chunk_words",
    "embedding_model", "source", "index"}` — reserved keys written **last**;
    `_flatten` drops `None`, joins lists of scalars with `"|"`, drops dicts;
  - `rag_chunk_over_budget` warning when `len(chunk)/4 > embed_max_tokens`
    (heuristic, documented as such);
  - the `rag.parse` span is opened **inside `iter_documents`, around the
    `await parser.parse(...)`**, so its duration is the parse and failed parses
    are recorded on it (status + error code); the pipeline only counts.
- `IngestReport.skipped_documents: int = 0`.
- `src/mangomas/rag/retrieval.py`: parser-derived chunks (metadata has `parser`)
  rendered inside `<untrusted-document source="…">…</untrusted-document>` with the
  `source` attribute HTML-escaped (`quote=True`) and every case-insensitive
  occurrence of the wrapper's opening or closing tag inside the body neutralised
  (`<` → `&lt;` for those tokens only, so ordinary content is unchanged); others
  rendered byte-identically to today. Adversarial tests: body containing
  `</untrusted-document>` (any case), source containing `"`, `>` and newlines.
- `src/mangomas/cli/commands/rag.py`: read parser from
  `orch.context.extras.get(PARSER_EXTRAS_KEY)`; pass to the pipeline; close it in
  `finally`; append ` skipped=N` only when `N > 0`. Help text and options
  unchanged (pins in `tests/constants/cli.py` hold).
- Docs: `mango-rag` skill section, `docs/rag/` page (rollback recipe, the
  embedding-model-change hazard, "parser off does not purge PDF vectors"),
  CHANGELOG.

**Tests** — one behaviour each (`tests/rag/test_pipeline_parsed.py`,
`tests/rag/test_chunker.py`, `tests/rag/test_retrieval.py`, `tests/test_cli_rag.py`):
1. `test_parse_failure_skip_preserves_existing_vectors` (pre-seeded
   `FakeVectorStore`, count unchanged);
2. `test_empty_markdown_still_purges` (today's behaviour, opposite direction);
3. `test_empty_parse_of_nonempty_file_is_failure_not_purge`;
4. `test_on_error_fail_raises_and_keeps_earlier_documents`;
5. `test_partial_success_indexed_with_status_partial`;
6. `test_parsed_table_keeps_newlines_and_separator` (blocker regression);
7. `test_all_failed_reports_skipped_not_empty`;
8. `test_flag_off_golden_tree_is_byte_identical` (report, ids, metadata, CLI string; no parser built);
9. `test_cli_closes_parser_on_success_and_error`;
10. `test_retrieval_unchanged_for_text_chunks` / `test_retrieval_frames_parsed_chunks`;
11. `test_reserved_keys_win` (both `source` and `index`) + Hypothesis
    `test_metadata_flatten_is_chroma_legal`;
12. `test_reingest_txt_then_parsed_replaces_cleanly`;
13. `test_chunk_lines_*` Hypothesis: whitespace preserved, full coverage of
    input, no table row split when it fits.
- Poisoned-PDF agent check stays in PR 2's bake-off (needs a real LLM).

**Mutation proofs:** #1 (two-sided with test 2), #3, #6, #9, #10 from the
delivery plan; record command + diff for each in the PR body.

---

## PR 8 — B4: deploy docling-serve · `ops/docling-serve-deploy` · L

Owner: `mango-ci-dev` + `mango-deploy` skill; review `/security-review`.
Depends on PR 1 (facts) and PR 5.

- Contract tests first, `tests/deploy/test_docling_serve_contract.py`:
  manifest exists; image referenced **by digest**; version ≥ floor from PR 1;
  `run.googleapis.com/ingress: internal`; no `allUsers` invoker; non-root
  `securityContext` (or recorded exception from PR 1); CPU/memory limits and
  `containerConcurrency` set; no literal secret/URL values; health probe path
  matches PR 1's finding.
- `deploy/docling-serve.yaml` (Knative Service, models baked, egress restricted,
  same region as data, body logging off).
- `.github/workflows/deploy-docling-serve.yml` (manual `workflow_dispatch`, WIF,
  digest-pinned actions — `test_workflow_hardening` applies); Trivy scan of the
  image (reuse the pinned `TRIVY_VERSION`/`TRIVY_SHA256` from the Makefile);
  Dependabot `docker` entry for the image.
- `deploy/README.md`: operator ingest recipe (Cloud Run Job or workstation with
  `MANGOMAS_PARSER__BASE_URL` + ID token), rollback, cost/cold-start budget,
  SLO (parse p95, skipped ratio alert). `deploy/service.yaml` untouched unless
  PR 1 decided the API service ingests.

---

## PR 9 — D1: in-process provider (opt-in, CLI-only) · `feat/docling-local` · M

Owner: `mango-rag-dev`. Depends on PR 7; parallel with PR 8/10.

- Clean-venv resolver check first (`docling==<pin>` + `chromadb` +
  `sentence-transformers`); on conflict, stop and record it in ADR-0036.
- `pyproject.toml` extra `docling = ["docling==<exact pin>"]` — own commit,
  `BREAKING-CHANGE: pyproject.toml — add the docling optional extra`; not in `dev`;
  regenerate locks per `tests/deploy/test_lockfile_freshness.py`.
- `adapters/parsers/docling_local.py`: the converter lives **inside a persistent
  child worker process** (`multiprocessing` `spawn` context), never in the
  Mango process: the parent sends `(suffix, bytes)` over a pipe and receives
  markdown or a typed error; resource limits (`resource.setrlimit` for address
  space and CPU on POSIX) are applied **in the child** before the converter is
  built; a wall-clock timeout kills and respawns the worker; the parent never
  imports `docling` (lazy import lives in the worker entry point, `# pragma: no
  cover - requires docling extra`). Windows: limits other than the timeout are
  unavailable — documented. Tests: worker crash, timeout kill-and-respawn,
  oversized output, limit enforcement (gated where it needs the extra).
- **CLI-only, enforced:** `docling_local` is *not* registered in the shared
  `_parser_registry` that `build_orchestrator` (and so the FastAPI lifespan)
  consumes. The RAG CLI constructs it through a dedicated caller-scoped factory,
  and `build_orchestrator` raises `ConfigError` if settings name it — tested
  through both `build_orchestrator` and `create_app`.
- Opt-in: refused unless `MANGOMAS_PARSER__ALLOW_IN_PROCESS=true` (new field,
  documented).
- Tests: missing-extra path via `monkeypatch.setitem(sys.modules, "docling", None)`;
  injected fake converter for semaphore/to_thread/format paths; gated
  `@pytest.mark.docling` smoke on one PDF.

---

## PR 10 — C: structure-aware chunking · gated on PR 2's decision rule

Not scheduled until PR 2's report exists. Sequence if the rule is met:
C1 markdown splitter with header repeat (pure, Hypothesis-tested) → re-run the
bake-off → C2 contextualised embedding (`_PreparedBatch.embed_texts`) only on a
measured gain → C3 HybridChunker spike only if a gap remains (adds
`docling-core[chunking]` extra with its own `pyproject.toml` trailer). If the
rule returns "inconclusive", extend labels before deciding; never record an
under-powered result as negative.

---

## Risk register

| Risk | Likelihood | Impact | Mitigation / owner |
|---|---|---|---|
| Serve facts differ from assumptions (auth, endpoints, limits) | Medium | High | PR 1 task 1.1 blocks PR 5; fixtures pin the contract |
| No Docker host available for PR 1/2 | Medium | Medium | Run on a dev workstation; or start `dockerd` in-session if permitted |
| Labelling effort slips | High | Medium (PR 10 only) | PR 2 off the critical path; B-track ships regardless |
| Coverage floor dip in `adapters` (85 %) | Low | Medium | respx tests; `mango-coverage-audit` on PR 5 |
| Trailer lost by squash merge | Medium | High (gate red post-merge) | Merge-commit rule on PRs 2, 4, 9 |
| Chroma rejects metadata shape | Low | Medium | `_flatten` + Hypothesis; verify against installed chromadb in `make rag` |
| Embedding-model change deletes a source | Low | High | Documented hazard; follow-up spec for a store capability protocol |

## Deferred (unchanged from the delivery plan)

Upload endpoint / agent tool; content-hash skip and fail-before-delete guard
(store capability protocol); retrieval dedupe/MMR; async serve endpoints; extra
formats (HTML, LaTeX, XBRL, METS, audio/video/email); VLM pipeline; `.mcp.json`.

## Verification

```bash
make gate                                              # every PR
BASE_REF=origin/feat/initial-release make protected-paths
make lint-imports
python -m pytest tests/adapters/parsers tests/rag tests/composition tests/deploy -q
make rag                                               # chromadb-backed suite
RUN_DOCLING=1 make docling-bakeoff                     # PR 2 / PR 10, Docker host only
```
