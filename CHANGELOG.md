# Changelog

All notable changes to Mango-Mas V2 are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

_Streaming turn persistence + metrics — Spec-0025 / ADR-0025 (governed
Batch B-a, `BREAKING-CHANGE`-trailered protected-path edit)._

### Fixed

- **Streamed conversations are no longer invisible.** `stream_dispatch`
  persisted nothing and the stream route recorded no metrics, so every SSE
  conversation was missing from `GET /history`, `SummarizeAgent`'s window,
  tenancy-scoped storage, and the agent instruments. `_stream_agent` now
  accumulates chunks and persists the turn on **full drain only**
  (abandonment and upstream errors persist nothing — a half-drained stream
  is not a turn; rule recorded in ADR-0025), and the stream route records
  the same invocation/error/duration metrics as invoke (full drain ⇔
  persisted ⇔ counted). The silent single-chunk degradation of
  non-streaming agents now logs a warning and is labelled.

### Added

- **SSE `metadata` terminal event** — `{"event": "metadata", "data":
  {"agent", "degraded", "chunks"}}` emitted after the token stream and
  before the byte-identical `done` frame (snapshot-tested), so consumers
  can detect degraded streams without any change to existing token events.
- **`Orchestrator.agent_supports_streaming(name)`** — the single additive
  public method (raises `AgentNotFound` for unknown names) letting
  transport layers label degraded streams without widening the protected
  `AsyncIterator[str]` contract. All dispatch signatures unchanged;
  persistence + abandonment guards mutation-proven; `core/orchestrator.py`
  and `api/routes/agents.py` at 100% branch coverage; harness
  `_traced_stream` composition covered.

---

_Structured-output validation + the shipped planner→tool→reviewer pipeline —
roadmap Phase 1 item 1.3._

### Added

- **`StructuredOutputAgent.parse()`** — the caller the planner/reviewer
  docstrings always promised: validates the agent's JSON reply against its
  own Pydantic schema and raises the existing `LLMBadResponse` (no new error
  type, no protected-path edit) with a truncated, content-free detail; the
  error log carries length + bounded head only, never user content.
- **`MANGOMAS_AGENTS__<NAME>__VALIDATE_OUTPUT`** (default `false`,
  `DEFAULT_VALIDATE_OUTPUT`) — opt-in per-agent validation: `handle()`
  validates after the LLM call and returns the raw JSON unchanged when
  valid, so the response contract is byte-identical for valid output and
  fully backwards-compatible when off. Streaming is deliberately untouched.
- **`examples/workflows/plan-execute-review.json`** — the canonical
  planner → tool → reviewer `WorkflowGraph`, loader-validated and executed
  end-to-end in tests (with validation on, including the failure path);
  documented in `docs/workflow/graphs.md`. The advertised multi-agent loop
  now ships instead of living only in documentation. Hypothesis fuzz proves
  `parse()` total over arbitrary text (nothing but `LLMBadResponse` or a
  model instance). Key guards mutation-proven.

---

_Deploy integrity + supply-chain baseline — Spec-0024 / roadmap Phase 0._

### Fixed

- **`deploy.yml` now applies `deploy/service.yaml`.** The deploy job
  previously ran an image-only `gcloud run deploy`, so none of the manifest's
  env vars, secret refs, probes, limits, or autoscaling bounds ever reached
  the service — a real deploy would have run with library defaults (auth off,
  SQLite, console exporter). The job now renders the manifest (image
  substitution proven in-job) and applies it with `gcloud run services
  replace`, gated by a new `verify` job (`make test` + `make coverage`,
  absorbing the deferred NEXT_STEPS item) and followed by an
  identity-token-authenticated smoke probe of `/healthz` + `/readyz`.
  Four new contract tests in `tests/deploy/test_deploy_contract.py` tie the
  workflow to the manifest in both directions — all mutation-proven.
  `deploy/README.md`'s local snippet taught the old image-only defect and is
  corrected. (Spec-0024.)
- **`eval-gate.yml` no longer defaults its external `ianshank/Agents` install
  to the moving `main` ref** — the job now fails closed with a clear error
  when `AGENTS_HARNESS_REF` is unset; pinned by
  `test_eval_gate_external_install_has_no_floating_default_ref`.
- **The FastAPI app version no longer drifts from the package** —
  `create_app` derives it from `importlib.metadata` (was hardcoded `0.1.0`
  against a `0.3.1` package), with a fallback constant for uninstalled
  checkouts and guard tests for both branches.
- **`rag/pipeline.py`'s "no chunks" warning no longer misattributes the skip
  to `min_chunk_words`** (the knob is provably inert); the message names the
  real cause (empty/whitespace-only document) and the pinning test was
  mutation-proven. README's `MIN_CHUNK_WORDS` row now mirrors CLAUDE.md's
  honest "currently inert" wording.

### Added

- **Supply-chain baseline** (roadmap 0.3): `requirements.lock` — the full
  pinned transitive runtime closure compiled by pip-compile under the image's
  Python 3.11, consumed as a pip constraints file by the Docker runtime
  stage; both Dockerfile `FROM` lines digest-pinned to the registry-verified
  `python:3.11-slim` index digest; a `pip` Dependabot ecosystem; a pinned
  `make pip-audit` target in the network group with a dedicated CI job —
  placement (out of `gate`, delegated to make) two-sidedly tested.
- **Docs/ledger truth sweep** (roadmap 0.4): specs 0019–0023 acceptance
  boxes adjudicated against the shipped tree with dated notes (spec-0021 →
  Implemented); stale Cloud-Trace "deferred" passages in
  `docs/architecture/observability.md` / `cloud-providers.md` rewritten;
  CLAUDE.md history rows now name the shipped `GET /history` route;
  spec-0019's plan-file reference fixed; the stale `.env.example`-drift
  record in NEXT_STEPS.md corrected (the drift was already fixed).

---

_Next-steps roadmap — a peer-reviewed case for the development program._

### Added

- **`docs/analysis/20260822-next-steps-roadmap-analysis.md`** — full-repo
  strategic review (three parallel surveys; load-bearing claims verified
  against source; adversarially peer-reviewed by `mango-architect`,
  verdict approve-with-changes, corrections folded in). Establishes the
  Phase 0–3 program — deploy integrity before the v0.4.0 cut, the governed
  protected-path batches, the shipped planner→tool→reviewer flow — plus the
  D1–D9 sponsor-decision register and an execution map onto the repo's own
  agent/skill corpus.
- **Spec-0024** (deploy-manifest application + post-deploy smoke) and
  **spec-0025** (streaming turn persistence + metrics, governed Batch B-a,
  reserving ADR-0025) — the two Phase 0/1 items that change shipped
  contracts, drafted per the spec-before-code convention; `specs/README.md`
  index and next-free counters updated.
- **`NEXT_STEPS.md`** gains a forward "peer-reviewed development program"
  section linking the tranches to the analysis doc, replacing ad-hoc
  forward ordering.

---

_Post-review hardening (peer review of spec-0022) — Spec-0023._

### Fixed

- **The injection guard added by spec-0022 was bypassable by deleting one
  space.** `test_no_run_body_interpolates_forbidden_expressions` matched the
  literal `"${{ github.event."`; GitHub allows arbitrary whitespace inside
  `${{ }}`, so `${{github.event.release.tag_name}}` — the exact line the test
  exists to forbid — passed it. Now a regex over the whole
  attacker-influenced family (`github.event`, `head_ref`, `ref_name`,
  `inputs`, `secrets`), with `test_forbidden_expression_pattern_is_whitespace_insensitive`
  pinning the guard's own weakness. The same file was blind to `*.yaml`
  workflows and to job-level `uses:` (reusable-workflow calls).
- **`mangomas <cmd> --verbose` stopped working mid-run.** Commands called
  `logging.basicConfig(level=DEBUG)` themselves; the first `get_tracer()`
  deep in dispatch then lazily called `configure_telemetry()` with its
  default `log_level="INFO"` and `force=True`, replacing the root handler.
  Every `logger.debug` after that vanished — exactly where the interesting
  work happens. Fixed by one reusable seam,
  `_runtime.configure_cli_logging`, pinned by `tests/test_cli_runtime.py`.
- **No CLI run had ever honoured `MANGOMAS_LOG__FORMAT` or
  `MANGOMAS_TELEMETRY__EXPORTER`** — and the seam above did not, by itself,
  fix that. `mangomas.telemetry.get_tracer` self-bootstraps
  `configure_telemetry()` with hard-coded defaults (INFO / text / console),
  and `configure_telemetry` is idempotent, so whoever calls it first wins.
  Four modules bound `_tracer = get_tracer(__name__)` at module scope —
  `eval/gate.py`, `eval/baseline.py`, `eval/sinks/langfuse.py` and
  `rag/pipeline.py` — and the CLI imports all four, so telemetry was latched
  at defaults before `main()` ran. The codebase already stated this rule and
  already tested it, but only against the `mangomas.api.app` import chain,
  which imports none of the four; that is why the drift went unseen. All four
  now use the house idiom (`opentelemetry.trace.get_tracer` inside the
  function, which returns a provider-deferring proxy), and
  `test_no_module_configures_telemetry_at_import` replaces the per-chain
  check with an AST scan over every module under `src/`. The log *level* is
  additionally re-applied after the idempotent call — the non-verbose path
  needed that as much as `--verbose` did — so it survives a latch the scan
  cannot prevent, such as a prior command in the same process.
- **The RAG *query* half stayed silent after the ingest half was fixed**, which
  left the three most-reported symptoms indistinguishable: an empty store, a
  document dropped at ingest, and a query that genuinely matches nothing all
  produced zero results and no explanation. `Retriever.search` now emits a
  `rag.search` span mirroring `rag.ingest`, warns on zero matches, and a
  `retrieve` tool call arriving with no query — a prompt or schema problem, not
  a miss — warns rather than only telling the model. The query text is never
  logged, only its length: it is end-user content and this layer cannot know
  what it carries. A test asserts that absence directly, so a later "just log
  the query, it helps debugging" edit fails rather than shipping user text into
  the log stream.
- **`rag/` was silent on the longest-running operation in the product.** No
  logger anywhere in the package, including a branch that drops a document
  from the index without a word — the "my file did not get indexed and I
  have no idea why" case. Now an `rag.ingest` span with per-batch children,
  and warnings on both silent-failure paths.
- **`asyncio_default_fixture_loop_scope` was unset**, so pytest-asyncio's
  announced default change would have landed on a minor bump rather than as a
  reviewed edit. Pinned to the announced future value; the suite is green under
  it.
- **An invalid escape sequence in a test module docstring** raised a
  `DeprecationWarning` on every run — the one warning the suite carried that
  was actually ours.
- **The Stop hook swallowed the zero-skip guard's signal.** `|| true` is now
  `|| exit 1`: only exit code 2 blocks a Stop hook, so non-zero was already
  a visible, non-blocking notice — the swallow just downgraded it to a
  transcript line.
- **`mango-config` would have failed CI if followed.** Its Workflow ended at
  `.env.example` while spec-0022 R12 made the CLAUDE.md config row mandatory.
- **`mango-ci-dev` was in neither half of the agent↔skill mapping**, so both
  skill-duplication guards were silently off for it. It now maps to
  `mango-deploy` + `mango-mutation-proof`, and
  `test_every_agent_is_mapped_or_recorded_unmapped` asserts the two sets
  partition the corpus, so the next agent cannot fall through the same way.
- **Three documentation claims that had drifted past what they describe**:
  `mango-ci-dev` did not list `nightly.yml` among the workflows it owns; the
  ecosystem doc still said `claude mcp list` shows 5 servers after `github`
  became the sixth (now pinned by
  `test_documented_server_count_matches_the_adopted_set`, since a count
  nothing compares is a claim rather than a check); and `NEXT_STEPS.md` listed
  `dependabot.yml` as deferred tooling after this branch added it.
- **`mango-pr-watcher` labelled two Claude Code platform tools as
  `mcp__github__*`** and did not say the agent is inert without a PAT.
- **`.PHONY` was missing `gated-suites`** — a CI-invoked target — and
  `embeddings-local`.

### Added

- **`CONTRIBUTING.md`.** A repo with five protected paths, a
  `BREAKING-CHANGE` trailer convention, spec-before-code, twenty coverage
  floors and a 27-agent corpus had no entry point telling a new contributor any
  of it. Written as a map that links to the file owning each rule, never a
  second copy of the rule — a duplicated rule is one that disagrees with the
  original within a release.
- **Link-integrity tests for the repo's own docs.** Every relative Markdown
  link in the current-state docs must resolve, and no bullet list may name the
  same target twice — the signature of someone appending to a "further reading"
  list without reading it. `docs/adr/` and `docs/plans/` are excluded: they are
  dated records that may name since-renamed paths, and rewriting them to please
  a linter would falsify the record. External URLs are not checked, so a third
  party's outage cannot turn this build red.
- **`docs/testing/regression.md`'s floor table is pinned to the gate.** The doc
  said "if this table and that script ever disagree, the script wins and this
  table is the bug" — honest, and an admission nothing checked it. Six enforced
  floors (`headers`, `entry_points`, `config`, `telemetry`, `metrics`,
  `harness`) were missing, so a reader auditing coverage policy saw fourteen
  where twenty exist. Checked in both directions: a phantom row overstates the
  policy exactly as a missing one understates it.
- **Nightly scheduled workflow.** The repo had no scheduled automation at
  all: `secret-scan` fired on push only, and seven opt-in suites ran nowhere,
  ever. `nightly.yml` runs the Postgres suite (Docker is all it needs — and
  ci.yml already records that a row-shape defect shipped behind a green
  pipeline "purely because nothing set the gate") and both gitleaks passes.
- **A guard that both secret-scan jobs check out full history.** Nothing
  asserted it. `fetch-depth` left at its default turns the gitleaks `git` pass
  into a one-commit scan that reports "no leaks found" and goes green —
  indistinguishable from clean history, and vacuous exactly where the pass
  matters, since its whole purpose is a credential committed and later removed
  from the working tree.
- **A failure path for the nightly run.** A scheduled workflow surfaces
  nowhere: GitHub emails only the account that last touched the cron, and only
  on the *first* failure of a consecutive run, so a suite that breaks and stays
  broken goes quiet after night one — the exact shape of the long-lived defect
  a nightly suite exists to catch. A `if: failure()` job now files one tracking
  issue, deduped by title so a week of red is one thread rather than seven
  issues, using the `gh` CLI and `GITHUB_TOKEN` already on the runner (no
  action to pin, no secret to provision).
  `test_every_scheduled_workflow_reports_its_own_failure` checks the guard
  rather than the job name, so the reporting job can be reimplemented freely
  and only deleting the failure path fails.
- **Tests for the coverage gate itself.** `scripts/check_coverage.py` sat at
  24%: a defect in `_check`/`main` would pass the whole per-package gate
  while measuring nothing, and no coverage number could reveal it. Now 100%,
  and `SCRIPTS_FLOOR` ratchets 84 → 92 on a measured 94%.
- **Property tests for `sanitize_header_token`** — the shared log-injection /
  SQL-parameter defence, previously guarded only by hand-picked examples.
- **`mango-api-impl-dev`**, and a test deriving source ownership from the
  agent corpus itself. The whole FastAPI assembly layer had no write-capable
  owner: `create_app` and its load-bearing middleware install order,
  `middleware.py`, `auth.py`, `health.py`, `tracing.py` and the system +
  workflow routers. `mango-api-dev` is a router and cannot edit;
  `mango-sse-streamer`, `mango-schema-evolution` and `mango-error-taxonomy-dev`
  each own one slice and correctly decline the rest. The corpus asserted it
  covered the codebase and nothing checked that, so
  `test_every_source_surface_has_a_write_capable_owner` now reads each agent's
  `## Surface You Own` — the agent bodies stay the single source of truth
  rather than gaining a parallel table — and requires every top-level entry
  under `src/mangomas/` to be claimed or recorded unowned with a reason.
  `registry.py` is the one recorded exception: a generic `Registry[T]` on a
  protected path, consumed equally by five registries, where naming any single
  owner would be arbitrary.
- **`mango-ci-dev`**, owning `Makefile`, `.github/workflows/`,
  `dependabot.yml`, `deploy/` and `tests/deploy/` — five contract suites and
  the whole gate chain belonged to no agent.
- **Documented procedures**: the `.claude/settings.json` lockstep (including
  that the repo denies itself `Edit` on that file) in `mango-harness`; the
  subprocess meta-test pattern in `mango-mutation-proof`; the OpenAPI-snapshot
  regen in `mango-release` and `mango-schema-evolution`.
- **`tests/deploy/_workflows.py`** — one workflow-YAML reader, replacing the
  copy that had drifted between the two suites.

### Changed

- **CLAUDE.md's config defaults are now actually enforced.** The
  "No hard-coded values" row cited a test that checked *names* in both
  directions while ~96 documented default values went uncompared. Defaults
  are what rots; they are now checked against the live `Settings` fields.
- **`MANGOMAS_RAG__MIN_CHUNK_WORDS` is documented as inert.** Unblinding the
  chunker fuzz (both properties pinned `min_words=0`, making the
  trailing-fragment drop unreachable) showed the branch is *provably* dead —
  zero reachable states over an exhaustive search, and 12k randomised
  comparisons where `min_words` never changed the output. Resolving it
  (accept word loss, or retire the knob) is a retrieval-quality decision, so
  today's behaviour is pinned and the doc row corrected.
- **Named the last two hard-coded tunables**: `core/tools.py`'s repeated
  truncation bound (kept local, since `core` imports nothing but `errors` and
  `registry`, with a test pinning it to the shared default) and
  `adapters/storage`'s triplicated `list_turns` limit.
- **The "no magic numbers in tests" rule is scoped to domain values**, which
  is what it always meant — `PLR2004` is deliberately off for `tests/*` and
  65 HTTP status literals sit inline by design.

_Governance-hardening adoptions (SSD-pack Tier 1 + 2) — Spec-0022._

### Fixed

- **`make secret-scan` ran the deprecated, history-only `gitleaks detect`.**
  An uncommitted `.env` holding a real credential passed the gate. The recipe
  now runs both supported passes — `gitleaks dir` (working tree) and
  `gitleaks git` (history) — locked by
  `test_secret_scan_runs_both_gitleaks_passes`.
- **`deploy.yml` interpolated event payload into a credentialed shell.**
  `${{ github.event.release.tag_name || github.sha }}` (and a secret) were
  expanded inside the `id-token: write` job's `run:` body — a release tag name
  is attacker-influenceable text. Both are now bound through `env:` (the
  `eval-gate.yml` idiom), and `tests/deploy/test_workflow_hardening.py`
  forbids `${{ github.event.* }}`/`${{ github.head_ref }}`/`${{ secrets.* }}`
  in any workflow `run:` body.
- **`scripts/harness_session_start.py` could not honor its own exit-0
  contract.** Module-scope `httpx`/`mangomas` imports died with
  `ModuleNotFoundError` on the exact machine the hook's warnings exist for (a
  fresh web session with no venv). Both imports are now guarded and the probes
  degrade to warnings; a subprocess test proves exit 0 on a bare interpreter.

### Added

- **MCP write/mutation deny rules** in `.claude/settings.json` — the ten
  `mcp__filesystem__*` write tools and `mcp__git__*` mutation tools bypassed
  both the PreToolUse Edit-matcher and the `Edit(...)` deny rules (ADR-0021's
  conceded MCP gap); denied now at the permission layer, the only
  in-session-authoritative one. `test_mcp_deny_rules_name_adopted_servers`
  guards against silently-inert typo'd rules.
- **Advisory Bash protected-path check** — the `--hook pre-tool-use` linter
  mode also inspects Bash `tool_input.command` and emits a mention-level
  `permissionDecision: "ask"` for protected paths (never `deny`, never a
  non-zero exit; ADR-0021's advisory/authoritative split unchanged),
  registered as a second hook under the `PreToolUse`/`Bash` matcher.
- **Zero-skip session guard** (`tests/conftest.py`) — an otherwise-green run
  fails on any skip outside the nine sanctioned env-gate reasons (now
  single-sourced as `ENV_GATE_SKIP_REASONS` in `tests/constants.py`) and on
  any xfail/xpass, including collection-level `importorskip` skips. Escalate
  only: a red run is never masked.
- **Collection-gate meta-test** (`tests/tooling/test_collection_gate.py`) —
  subprocess pytest over tmp mini-suites importing the real conftest hooks
  proves the gate skips/unskips, the guard escalates, and mutating
  `session.exitstatus` really changes the process exit code.
- **Three contract tests for asserted-but-untested behavior** — a normalized
  OpenAPI-projection snapshot (`tests/test_openapi_snapshot.py`, regen via
  `python -m tests.test_openapi_snapshot`); the mid-stream SSE truncation
  contract (token frames delivered, no `done`, no invented error frame),
  observed at the raw ASGI boundary; and an exhaustive 17-subclass
  `MangomasError` → intended-HTTP-status walk that fails on any new subclass
  without a recorded decision.
- **Reverse config-doc drift test** —
  `test_claude_md_documents_every_settings_field` closes the direction the
  existing contract missed; CLAUDE.md's tables gained the 29 missing rows.
- **`docs/plans/_template.md`** — plans were the one artifact in the
  specs/ADR/plans triad without a template; includes the
  failing-test-first-per-milestone and honest-dependency conventions.
- **WHEN/THEN Scenarios section** (optional) in `specs/TEMPLATE.md`, with the
  both-directions fail-closed rule the 0020/0021 defect hunts motivated.
- **Adversarial review protocol** in `mango-architect`'s output format —
  severity enum, confidence tags, a 2-fix-cycle escalation cap, and a
  red-stage mode for tests-only diffs.
- **"Enforced by" column** in CLAUDE.md's Key Design Rules — each invariant
  names its mechanical gate, or honestly says "code review (prose-only)".

### Changed

- **Third-party GitHub Actions SHA-pinned** (`codecov-action`,
  `google-github-actions/auth`/`setup-gcloud`), with `.github/dependabot.yml`
  (github-actions ecosystem, monthly) as the bump mechanism; first-party
  `actions/*` stay tag-pinned by policy, asserted by
  `test_third_party_actions_are_sha_pinned`.
- **`pytest-cov`/`coverage` exact-pinned** in the dev extra (lockstep-comment
  idiom) so the measured coverage denominator cannot drift between
  environments.

_CI/Makefile parity and corpus-validation completion — Spec-0021._

### Fixed

- **CI's `pull_request` trigger named branches that don't apply here.**
  `branches: ["main", "develop"]` — `develop` doesn't exist anywhere in this
  repository (checked against local and remote branches), and `main` has
  genuinely diverged from trunk (100 commits one way, 11 the other; full
  reconciliation is tracked separately in NEXT_STEPS.md). Every other base-ref
  in this repo — the Makefile's `BASE_REF`, the `protected-paths` job's
  hardcoded `BASE_BRANCH` — already treats `feat/initial-release` as trunk;
  the PR trigger was the one place still pointing elsewhere, so a PR opened
  against the real trunk got no `pull_request`-triggered CI at all. Fixed to
  `["feat/initial-release"]`, locked by
  `test_pull_request_trigger_targets_the_real_trunk`.
- **`secret-scan` was the one CI job with no Makefile target.** `lint`, `test`,
  `protected-paths`, `bridge-coverage` and `scripts-coverage` each run
  `make <target>` — a parity `tests/deploy/test_ci_make_parity.py` already
  asserted for all five. `secret-scan` instead ran raw inline `curl`/`gitleaks`
  shell, with `GITLEAKS_VERSION` hardcoded only in the YAML — falsifying
  README's own claim that "the Makefile wraps the exact commands CI runs, so
  one target reproduces the whole pipeline locally." Fixed with a new
  `make secret-scan` target (`GITLEAKS_VERSION ?= 8.21.2`) that `ci.yml` now
  calls as its one step, locked by `test_secret_scan_job_delegates_to_make`.
  Deliberately **no** skip-if-cached guard: checking only executability, not
  version, would let a future `GITLEAKS_VERSION` bump silently keep running a
  stale cached binary — a correctness bug specifically dangerous for a secret
  scanner. The target always re-fetches, matching CI's ephemeral-runner
  behaviour exactly.
- **`AGENT_SKILL_OWNERS` values were never checked against the live skill
  roster.** The existing test only asserted a mapped skill name is a substring
  of the owning agent's body prose — a renamed or retired skill cited only in
  stale prose still passed. `test_agent_skill_owners_resolve_to_a_real_skill`
  now resolves every mapped value against `EXPECTED_SKILL_SLUGS` directly.
- **The live `.claude/` corpus's full Pydantic schema never ran under
  pytest.** `run_schema_lint()` — exactly what `make frontmatter` calls — was
  previously exercised only as a separate non-pytest step, or against
  synthetic fixtures. A corrupted skill/agent frontmatter file would pass all
  27 pre-existing `test_corpus_contract.py` checks; mutation-proving the new
  `test_live_corpus_passes_schema_lint` confirmed exactly that gap — every
  other test in the file stayed green while the corruption slipped past it,
  until this test caught it with a named, readable failure.

### Changed

- **`docs/architecture/c2-container.md` and `README.md` catch up to the
  `cli/` package split** landed on trunk by PR #35 (spec-0015 / ADR-0019).
  Both now name the permanent re-export facade pattern the way `config/` and
  `telemetry/`'s entries already do, matching `CLAUDE.md`'s architecture tree.
  README also gains a note that `make secret-scan` is CI-only and
  deliberately outside `make gate` — every other gate step runs fully
  offline, and downloading a pinned release binary is the one exception.

_Gate integrity and corpus completion — Spec-0020._

### Fixed

- **The coverage floor list had no completeness guard.** 19 floors covered every
  top-level path under `src/mangomas/` except `_entry_points.py`, and nothing
  asserted it — a package added tomorrow would inherit only the 95% global,
  which is an *average*, so a small module at 40% moves it by a fraction of a
  point and the gate stays green. This is the fourth defect of one shape, after
  a flat `api/*.py` glob, a non-recursive `cli` glob, and an over-matching
  ellipsis exclusion. `tests/test_check_coverage.py` now names that invariant
  class in its docstring and owns every guard for it, so the fifth instance
  lands somewhere obvious instead of being rediscovered.
- **`mypy` checked a different surface for developers than for CI.** A bare
  `python -m mypy` covered **155** files (`packages = ["mangomas"]`) while
  `make typecheck` covered **324**. CLAUDE.md documents the bare command, so
  following the docs gave a weaker check than the gate, with tests, scripts and
  the eval bridge untyped locally. Fixed in configuration rather than
  documented as a caveat — a `files` list mirroring the Makefile's `CODE_PATHS`
  — and locked by two assertions in `tests/deploy/test_ci_make_parity.py`,
  separate because a leftover `packages` key would re-narrow the surface even
  with `files` correct.

### Changed

- **Nine ruff rule families adopted as a ratchet**: `LOG`, `G`, `ASYNC`, `ERA`,
  `DTZ`, `TID`, `C4`, `PTH`, `T20`. Every family was measured across all four
  lint paths first and eight were already at zero, so this locks in properties
  the code already has rather than demanding a cleanup. Three real hits, all
  semantics-preserving. **`ASYNC240` is excluded by name**: it recommends
  `trio.Path`/`anyio.path`, this project is asyncio-only, and all four hits are
  synchronous `Path.read_text()` in async *test assertions* — obeying it would
  mean taking a dependency to satisfy a linter. `N`, `TRY` and `FBT` are
  recorded as deliberate non-goals; `N818` alone would demand renaming
  `AgentNotFound` and four siblings on a protected path.
- **Two owner agents** — `mango-cli-dev` and `mango-harness-dev` — close the
  last five surfaces with no write-capable owner. spec-0019 deferred `cli/`
  because its shape was about to change; it has now settled.
  `_entry_points.py`, `correlation.py` and `_headers.py` join existing owners
  rather than getting agents of their own. Roster 23 → 25.
- **Two skills from procedures that produced findings**:
  `mango-mutation-proof` (a green test proves nothing unless you know what
  makes it red — including how to pick a mutation that *discriminates*) and
  `mango-coverage-audit` (compare coverage's own parser against its report; an
  over-matching exclusion makes the percentage go *up*, so the symptom looks
  like success). Roster 13 → 15.
- **Hooks**: `ruff format` now runs beside `check --fix` on edit — the one check
  a per-file hook could not do — and `make typecheck format-check` (0.3s warm)
  runs ahead of the Stop suite, catching cross-file type breakage that neither
  the ruff hook nor pytest sees. Adding `--cov` there was measured and rejected:
  +14s per turn end for a signal that would not have caught any of the three
  coverage defects above.

_Package decomposition — Spec-0015 / ADR-0019._

### Changed

- **Three oversized modules are now packages behind permanent re-export
  facades** (ADR-0019). `config.py` (593 lines, the repo's #1 churn file) →
  `config/` as 12 domain modules; `telemetry.py` (337) → `telemetry/` cut by
  dependency layer rather than by telemetry signal, which would have split the
  shared exporter vocabulary across tracing and metrics; `cli/main.py` (682) →
  `cli/commands/` plus `_runtime`, `exit_codes` and an `_app` assembly root.
  **No import changes**: `from mangomas.config import Settings`,
  `from mangomas.telemetry import get_tracer` and
  `from mangomas.cli.main import app` all resolve to the same objects they did
  before, and `tests/test_import_compat.py` asserts identity — not equality —
  for each. The console script is untouched; `mangomas --help` renders the same
  commands in the same order, which `tests/test_cli_surface.py` now pins,
  including the order (Typer lists in *registration* order, so an import
  reshuffle could otherwise have rewritten it silently).
- The CLI cut is by **dependency layer, not command group**: `_build`,
  `_close_orchestrator` and the three exit codes are needed by every group, so
  a per-group split would have orphaned them and forced command modules to
  import each other. `commands/_eval_config.py` is a second cut inside the eval
  group — without it `commands/eval.py` lands at ~395 lines and is still the
  largest module in `src/`.
- **`orchestrator_session()` (spec-0015 R1) is deliberately not included.**
  Module-attribute resolution solves the patch-seam problem it was reaching for,
  and adding it would have moved `_build()` into the event loop and rewritten
  seven command bodies — turning the split into something other than a pure
  move. Recorded as an amendment in the spec, not dropped.
- `tests/constants.py` re-exports the three CLI exit codes from
  `mangomas.cli.exit_codes` instead of restating `3`, `2` and `1` as literals
  under a comment promising they match. Only possible after the split: the codes
  used to live in `cli/main.py`, and importing that from the constants hub would
  pull the whole command tree and four eval-registry side-effect imports into
  every module that reads a constant.

### Fixed

- **Thirteen CLI tests were passing against the wrong system.** Measured, not
  suspected: with the orchestrator patches neutralised and a counter on
  `build_orchestrator`, 13 of 15 `monkeypatch` sites turned out to have no
  effect. Those tests were constructing *real* orchestrators — opening `httpx`
  connections to `localhost:1234` and creating SQLite files — and passing
  anyway, because they asserted things the real system also produces
  (`test_agents_command` checks that `chat` appears in the output; the real
  registry contains `chat`). `tests/_seam_guards.forbid_real_orchestrator` makes
  that failure loud, and resolves its target through `_build.__module__` so it
  followed the function into `_runtime.py` with no edit.
- **Six coverage gaps the `cli` package aggregate was hiding.** 97% across the
  package looked healthy; per module it was `_runtime` at 88% — including
  `_build()`, the one line no test executed because every suite replaces it —
  plus `_finish_eval`'s untested exit-1 sink-error path, the `--output-json`
  override branch, and `--verbose` on four commands. All closed; `cli` reaches a
  measured 100% statements and 100% branches — measured being the operative
  word, see the exclusion fix below.
- **`exclude_lines` silently deleted whole CLI command bodies from coverage.**
  `"\\.\\.\\."` is there for Protocol stub bodies, but unanchored it matches any
  line with three dots — including every `typer.Argument(..., help="…")` in a
  command signature. Coverage drops the entire block when the excluded line
  belongs to a `def` header, so `cli/commands/rag.py` reported **16 statements
  where coverage's own parser sees 53**, and two `--verbose` branches that no
  test invokes sat inside the invisible region while the file reported 100%.
  Now anchored to the end of a line, in the two forms `src/` actually uses: 30
  bare-line stubs and 3 inline (`def get(...) -> str | None: ...`), the latter
  found because dropping it broke the `secrets` package's 100% floor. Honest
  measurement *raises* the global figure — 98.53% → 99% — because the
  newly-visible command bodies were mostly well tested; the danger was never a
  low number, it was a number computed over the wrong denominator. Pre-existing,
  not introduced by the decomposition. `tests/test_check_coverage.py` guards
  both directions by matching the configured patterns against real source lines
  rather than asserting their shape, which any differently-worded bad regex
  would pass.
- `logging.getLogger` in the eval command is pinned to the pre-split
  `"mangomas.cli.main"` rather than `__name__`. A refactor promising no
  behaviour change must not rename a field operators filter on.

_Live Claude Code corpus — Spec-0018 / ADR-0024._

### Changed

- **Skills own procedure; agents own a surface** (spec-0018 R7), now enforced by
  four tests rather than by review. The agent corpus carried seven skill
  references in total, twelve agents cited none, and the worst offender
  duplicated 70 % of its body from the skill it named. Agent corpus 1230 → 1031
  lines; skill references 7 → 26. Agent heading vocabulary collapses from 56
  distinct headings to 9 — ad-hoc names are where a duplicated section hides.
- **New `mango-harness` skill** owning protected-path governance: the
  `BREAKING-CHANGE` trailer, and the advisory-hook-vs-authoritative-gate
  distinction. It absorbs a block that was byte-identical across four agents.
- **Two nested `CLAUDE.md` files** (`tests/`, `src/mangomas/core/`) replace five
  stray `agent.md` files; the other three were deleted as skill duplicates.
- **All 19 agents are now live at `.claude/agents/mango-<slug>.md`** — one flat
  directory, no parent/child hierarchy. This is the capability change: agents
  are discoverable, auto-delegated from their descriptions, and bounded by
  explicit `tools:` lists. Flattening also retires an unverified premise —
  ADR-0024's stated benefit assumed VS Code Copilot recurses the agents
  directory, which spec-0018 never established. A flat directory cannot
  reproduce the nesting defect in either tool.
- **`permissions.deny` gains `Edit(/.claude/settings.json)` and
  `Edit(/.claude/settings.local.json)`** — the two settings files, deliberately
  not `.claude/**`. Nothing legitimately needs Claude Code to rewrite its own
  permissions mid-session; a whole-tree rule would additionally have blocked
  every edit to `.claude/skills/`, which is ordinary authoring work. Cheap and
  partial rather than airtight: neither rule stops a `Bash` heredoc or `>`
  redirect.
- **The agent roster is scoped to tracked files.** Claude Code's `/agents`
  writes personal agents into `.claude/agents/`, so a glob-based roster would
  have red-lighted `make gate` for a contributor who did nothing wrong.
- **`.mcp.json` gains a sixth server, `github`**, pinned to
  `ghcr.io/github/github-mcp-server:v0.20.1`. It is **optional**: without docker
  and a `GITHUB_PERSONAL_ACCESS_TOKEN` it fails to start and the other five are
  unaffected. The npm `@modelcontextprotocol/server-github` package — which
  would have matched the npx form of every other server — is **deprecated
  upstream** and was deliberately not used.
- **All 19 agents converted to the Claude Code frontmatter schema**, in place at
  `.github/agents/` while the corpus is still inert. The move that makes them
  live is deliberately a separate change: Claude Code globs
  `.claude/agents/**/*.md`, and `.agent.md` *is* `.md`, so a move-first sequence
  would have made 19 agents live carrying `tools: [read, edit, search, execute]`
  (Copilot aliases Claude Code does not recognise) and
  `model: Claude Sonnet 4.5 (copilot)` — with `make gate` green either way,
  since it never exercises Claude Code's loader.
- **Agent identities are now kebab-case slugs** matching their filename stems,
  replacing Title Case names (`Backend Developer` → `backend`,
  `Schema Evolver` → `schema-evolution`). This renames every agent as a human
  refers to it. Claude Code resolves an agent by its `name` field, and the lint
  now requires name and filename stem to agree.
- **`argument-hint` is dropped from all 19 agents.** It is a valid VS Code
  Copilot field with no Claude Code equivalent; recorded as a loss rather than
  a cleanup.
- **Descriptions rewritten around routing.** Four routers keep a `Use when:`
  trigger list, name the agents they route to, and cannot write. The other 15
  carry ownership statements with **no trigger conditions at all** — phrasing
  like "invoke explicitly when X" is not a control, because auto-delegation
  matches X and never reads the modal verb. Capped at 320 characters, which
  binds on `backend`; total drops from 6,180 to ~4,600.
- **Stale citations repaired in both trees.** 13 `api/app.py` references across
  five agents, and seven in `.claude/skills/mango-error/SKILL.md` — live since
  B1 and carrying the identical wrong pointer. `_ERROR_STATUS` now lives in
  `api/errors.py`; `app.py` still re-exports it, so path-existence checks could
  never have caught this. `mango-agent-add/SKILL.md` cited
  `composition.py lines 102-106`; the registrations are at 240–244. No prose
  line-number citation remains in either tree.
- `_register_workflow_routes` → `build_workflow_router` in `api-dev`.

- **Skills moved from `.github/skills/` to `.claude/skills/`.** Claude Code
  reads nothing from `.github/`, so all 12 skills were inert for the tool this
  project uses. They are *not* an invented format — `.github/skills/<name>/SKILL.md`
  is a real, documented GitHub Copilot surface and every skill was fully
  conformant. Because VS Code Copilot also scans `.claude/skills/`, the single
  tree now serves Claude Code **and** VS Code Copilot; only the github.com
  cloud-agent surface is given up. Listed under `Changed` rather than `Removed`
  because the capability moves rather than disappears. Recorded as pure renames
  (100% similarity) so `git log --follow` and in-flight rebases survive.
- `.pre-commit-config.yaml`'s frontmatter-hook `files:` pattern is now
  path-anchored. The previous `(\.agent\.md|SKILL\.md)$` was filename-only and
  unanchored, firing on any similarly-named file anywhere in the tree while
  asserting nothing about location.
- `.github/copilot-instructions.md` claimed a **85 %** coverage gate; the real
  gate is 95 % global plus per-package floors, with `scripts/check_coverage.py`
  as the authoritative source.

### Added

- **Four owner agents close the deferred "B5" gap** (spec-0019):
  `mango-rag-dev`, `mango-eval-dev`, `mango-secrets-dev` and
  `mango-agent-impl-dev`. The 2026-08-09 delivery plan named seven unowned
  surfaces and deferred them; `eval/` (38 files, four plugin registries) and the
  RAG stack (14 files) were the two largest subsystems in the tree with a mature
  skill and no agent, so `mango-backend` — whose own description lists RAG in
  scope — routed that work to nobody. Roster 19 → 23, write-capable 12 → 16.
  `config.py` and `cli/main.py`, the other two B5 surfaces, are deliberately
  **not** included: both are spec-0015 decomposition targets, so an owner
  authored now would describe a shape about to change.
- `mango-secrets-dev` maps to **two** skills, `mango-adapter` **and**
  `mango-config`. `mango-adapter` alone is not merely thin here, it is wrong in
  two places: its "register the factory" rule contradicts `secrets/registry.py`,
  which stores provider *instances*, and its "all public methods are `async def`"
  rule contradicts `secrets/provider.py`, which is sync-only by design. Both
  contradictions are recorded as invariants in the agent body so a reader who
  follows the skill is corrected rather than misled.
- **Tenancy is documented in the corpus for the first time.** `tenancy.py` and
  `TenancyMiddleware` are a complete ADR-0017 subsystem with a 100% coverage
  floor and had **zero mentions across all 19 agents and 13 skills**.
  `mango-storage-adapter-dev` — which owns both files implementing the tenant
  row filter — now states that scoping is a row filter rather than a signature
  change, and that a new backend needs the idempotent column migration.
  `mango-api-dev` names `TenancyMiddleware` alongside the three opt-in
  middlewares it already listed.
- New agents cite settings by dotted import path (`mangomas.config`) rather than
  bare filename, so spec-0015's package split cannot invalidate them.
- No file is claimed by two write-capable agents: `agents/_streaming.py` stays
  with `mango-sse-streamer` (which already claimed it in both its description
  and its Surface table) and `ExecutionPlan`/`ReviewResult` stay with
  `mango-schema-evolution`. `mango-agent-impl-dev` names those owners instead.


- Permission-rule guards: deny set-equality, path-scoped rules must be
  `/`-anchored (an unanchored rule is cwd-relative and silently stops matching
  from a subdirectory), and no MCP `env` value may be a literal — every
  credential must arrive as a `${VAR}` interpolation. The old
  "no MCP server declares an API key" assertion is retired: the property worth
  keeping was never "no secrets" but "no *unreviewed* secrets".
- **Agent contract tests** (`tests/tooling/test_corpus_contract.py`), parametrised
  off the linter's own glob so they survive the pending move without edits.
  Assert the roster by set equality, that write capability matches a reviewed
  set (12 of 19 — a reviewed-change gate, not a deny-list substitute), that
  routers hold no `Edit`/`Write`/`Bash`, that only routers carry trigger
  conditions, and that the four protected-path owners name the
  `BREAKING-CHANGE` trailer — none of the 19 mentioned it before.
- **Claude Code agent-format validators** in `scripts/lint_agent_frontmatter.py`.
  `permissionMode`/`hooks` are rejected by a policy table rather than
  `extra="forbid"`, which would report "Extra inputs are not permitted" for a
  field Claude Code genuinely accepts; `argument-hint`/`sub_agents` get a
  separate "not a Claude Code agent field" message. `Agent(a, b)` tool scoping
  is rejected outright: it is silently ignored inside a subagent definition, so
  the agent would receive unrestricted delegation rather than the named subset.
- `tests/tooling/test_corpus_contract.py` — the corpus had no test asserting it
  existed, was non-empty, or lived anywhere in particular. Asserts the skill
  roster by **set equality** (a count names nothing; a set difference names the
  skill that appeared or vanished), that the retired tree stays deleted, that
  each directory name matches its frontmatter `name`, and that no
  contributor-facing doc points at a retired path.

### Fixed

- **A GCP + auth deployment rejected every request.** `create_app` resolves the
  expected API token during app *construction*, but the `gcp` secrets provider
  was registered only inside `build_orchestrator`, which the FastAPI lifespan
  calls strictly later. `resolve_auth_state` looked up an unregistered provider
  and took its fail-closed branch, yielding `expected_token=None`. The lazy
  registration is now the public, idempotent
  `composition.ensure_secrets_provider`, called from both entry points. Local
  runs never hit it: the `env` backend is seeded at import time.
- **Postgres returned `str` where SQLite returns `dict`.** The jsonb codec
  registers `encoder=json.dumps`, but `save_turn` bound `model_dump_json()` —
  already serialised — so the value was encoded twice and `list_turns` read back
  a string, breaking the row-shape parity the codec's own comment promises. Now
  binds `model_dump(mode="json")`. No test covered the shape, and the suite that
  would have is gated behind `RUN_POSTGRES=1`, which CI never sets.
- `/readyz` is unauthenticated and emitted unbounded `str(exc)` from a driver
  into the public body; both sites now truncate to
  `DEFAULT_ERROR_DETAIL_TRUNCATE`.
- `harness/__init__.py` was the one file of 133 missing
  `from __future__ import annotations`. `ruff`'s `required-imports` now enforces
  the rule that was previously prose in `CLAUDE.md`.
- The CLI's `history --limit` hardcoded `10` while its documented HTTP twin read
  `api.history_default_limit`, so the env override worked on only one surface.

### Changed

- **CI now runs the two gated suites that need no external service** —
  `tests/integration/` (ASGI in-process) and `tests/rag/` (fakes only), 38 tests
  that were excluded purely because nothing set their env gate. `make rag` is
  split from a new `make embeddings-local` target, which is the half that
  genuinely needs an extra.
- The tenant column default is interpolated from `DEFAULT_TENANT` rather than
  repeating the literal `'default'` across four DDL sites; rendered SQL is
  unchanged.
- `agents/_prompt.resolve_sampling` replaces the same two-line `AgentSettings`
  extraction duplicated in four agent constructors.

- **The corpus said things that were not true.** `mango-error`'s live SKILL.md
  and five agents pointed at `api/app.py::_ERROR_STATUS`, which moved to
  `api/errors.py`; `src/mangomas/errors.py`'s own module docstring said the same.
  `mango-pr-watcher` credited the frontmatter-lint checklist to `mango-testing`,
  where it does not exist — it is `mango-release`'s. Five dormant `agent.md`
  files documented a `TurnRepository.save()` and a `Turn` type that exist
  nowhere in `src/`, an SSE wire format a client could not parse, and a
  prompt-injection code block no agent implements.
- **A CHANGELOG convention nobody followed.** `### Breaking Changes` was
  prescribed in four places and used here zero times; it is not a Keep a
  Changelog section either. Replaced by what is actually enforced — a
  `BREAKING-CHANGE` commit trailer, plus `### Changed` with a
  backwards-compatibility note.

- **`--min-agents`/`--min-skills` accepted values that defeat the floor.** Bare
  `type=int` meant `--min-agents -1` passed and `_below_floor` could never fire,
  reinstating the silently-passing gate spec-0018 R1 exists to kill. Zero is
  rejected for the same reason. The test that covered the flags had *enshrined*
  the bug: it passed `0` against an empty directory and asserted `EXIT_OK`.
- **`Bash(python -m ruff *:*)` was a dead allow rule.** Claude Code honours only
  a trailing `:*` in a Bash rule, so the interior `*` was a literal and
  `ruff check --fix` prompted on every run. `tests/tooling/test_claude_code_settings.py`
  had zero assertions about `permissions`, so all three deny rules could have
  been dropped silently; deny set-equality, trailing-wildcard and
  inert-`Write(`/`NotebookEdit(`-head guards now cover it.
- **A YAML syntax error in frontmatter surfaced as a traceback.** `ScannerError`
  is not a `ValueError`, so an unquoted `description:` containing a colon
  escaped the caller's handler instead of being reported against the file.
- A green *skip* in the doc-pointer test: a typo'd or renamed entry in
  `CORPUS_DOC_RELPATHS` left that document unchecked while the suite still
  reported success.


- **The frontmatter gate could not detect its own irrelevance.**
  `scripts/lint_agent_frontmatter.py::main` globbed both corpus trees and, when
  zero files matched, fell through to `logger.info("Frontmatter lint passed")`
  and returned `EXIT_OK`. The counts went into an `extra={}` the log format
  drops, so they were never printed, and no test asserted a non-zero file
  count — so relocating the corpus would have produced a green CI validating
  nothing. Adds `MIN_AGENT_FILES`/`MIN_SKILL_FILES` floors (with
  `--min-agents`/`--min-skills` overrides), a `run_schema_lint() -> LintResult`
  seam so the counts are assertable at all, and count reporting in the log
  message. Landed *before* the move, so the move is verified rather than assumed.

_Protected-path governance contract — Spec-0017 / ADR-0021._

### Fixed

- **Protected-path `PreToolUse` hook was silently inert.** It read
  `$CLAUDE_TOOL_INPUT_path`, an environment variable Claude Code does not
  define (hook input arrives as JSON on stdin), so the check always resolved
  to "no path" and returned `EXIT_OK`. Independently, importing `pydantic`
  at module load time crashed the hook (exit 1, non-blocking) in an
  interpreter without the dev extras installed. `scripts/lint_agent_frontmatter.py`
  gains a stdlib-only `--hook pre-tool-use` mode reading the real stdin JSON,
  with `pydantic`/`pyyaml` imports deferred so the mode never needs them.
  The `PostToolUse` ruff-autofix hook had the identical defect; fixed the
  same way via `--hook post-tool-use --emit-path`.
- **Authoritative enforcement moved to CI.** `--check-protected-paths`
  (staged-diff based) cannot be a complete gate at `PreToolUse` time — the
  diff is empty before a file is staged, and the `Edit|Write` matcher never
  covered `Bash`/MCP filesystem tool calls. New
  `scripts/check_protected_paths.py` (`make protected-paths`, its own CI job)
  reads `git diff`/`git log` between the PR base and head, requiring a
  `BREAKING-CHANGE` marker in a **commit message**, not diff content (the old
  check passed if the marker string appeared in a *deleted* diff line). The
  `PreToolUse` hook is now advisory-only (`permissionDecision: "ask"`).
- **Harness streaming span never covered token emission.**
  `_HarnessOrchestrator.stream_dispatch` opened a span and returned an
  unconsumed async generator from `Orchestrator.stream_dispatch` — the span
  closed at iterator construction, before any token flowed. Rewritten as a
  `_traced_stream` generator that opens the span once, attaches/detaches OTel
  context per chunk (never across a `yield`, which would otherwise leak the
  harness span into every span the consumer subsequently creates), and ends
  the span on full drain, an upstream error, or early consumer abandonment
  (`aclose()`) alike. `api/routes/agents.py`'s SSE endpoint now wraps its
  consumer loop in `contextlib.aclosing` so a client disconnect closes the
  stream promptly rather than eventually via GC.

### Added

- `[tool.mangomas.governance]` in `pyproject.toml` — the protected-path set
  and `BREAKING-CHANGE` marker aliases, the single source of truth read (via
  stdlib `tomllib`) by `scripts/check_protected_paths.py`,
  `scripts/lint_agent_frontmatter.py`, and the new
  `src/mangomas/harness/governance.py`.
- `src/mangomas/harness/` package (`governance.py`, `config_audit.py`),
  ported from `origin/main`'s harness-hardening layer and adapted to this
  branch's governance model. `main`'s `coverage.py`/`harness_stop_gate.py`
  were deliberately not ported — `read_coverage_floor` parses
  `--cov-fail-under` out of `pyproject.toml` and feeds it back to a pytest
  run whose addopts already set that value, a tautology given
  `scripts/check_coverage.py` is already this branch's documented single
  source of truth.
- `scripts/harness_config_audit.py` — the `ConfigChange` hook, evaluating
  `mangomas.harness.config_audit.evaluate_config_change` against the new
  `HarnessSettings.config_audit_mode` (`MANGOMAS_HARNESS__CONFIG_AUDIT_MODE`,
  default `off`). Its `mangomas.config`/`mangomas.telemetry` imports are
  deferred so it degrades to the default mode rather than crashing when
  `mangomas` isn't importable.
- `make scripts-coverage` + its own CI job — `scripts/` sits outside
  `--cov=mangomas`'s reach, so it was entirely unmeasured; floor set to the
  measured actual (not an assumed 95 %) with a ratchet note.
- `docs/adr/0021-protected-path-governance-contract.md` and
  `docs/adr/0023-workflow-implementation-reconciliation.md` — the latter
  resolves the ADR-number collision with `main` (`0007`/`0011`) and records
  the disposition of the `main` ↔ `feat/initial-release` `workflow/`
  divergence: this branch's bounded-tree implementation is kept; a future
  `dag` node kind would compile to a `Sequence`/`FanOut` tree at load time
  rather than embedding `main`'s runtime scheduler, which would violate
  ADR-0011's acyclic-by-construction invariant.

_Code hygiene & modularity overhaul — Spec-0014 / ADR-0019._

### Fixed

- `ToolAgent`: no longer discards the tool-format system prompt when a custom
  `system_prompt` is configured — both are sent (custom first), so the LLM
  always learns the tool-call JSON contract.
- `ToolAgent`: honours `max_tool_steps` exactly — at most N LLM calls per
  request (previously up to N+1) and `metadata["tool_steps"]` reports the
  actual number of calls made.
- `make rag`: now passes `--no-cov` like every other opt-in suite target, so
  the RAG suite can run standalone without tripping the 95 % coverage gate.
- `scripts/run_workflow_e2e.py`: the orchestrator (and its LLM httpx pool) is
  closed in a `finally`, so workflow failures no longer leak connections.
- LM Studio LLM/embeddings adapters and the SQLite repository no longer embed
  unbounded upstream bodies in client-visible error messages — the body is
  truncated to `DEFAULT_ERROR_DETAIL_TRUNCATE` and carried in `detail`.
  Persistence-failure logging unified on `logger.exception` (SQLite/Postgres).
- The lazy metrics-instrument singleton is built under a lock (double-checked),
  so concurrent first records can no longer register duplicate instruments.
- `IngestReport.deleted_sources` counts sources whose vectors were actually
  deleted instead of always equalling `documents`;
  `VectorStoreRepository.delete_by_source` now returns the number of vectors
  removed.
- `FileMemoryRepository`: honours its closed state — post-`close()` reads and
  writes raise `PersistenceError`, matching `SQLiteRepository`.
- Workflow `fan_out` join and `sequence` final return now execute inside their
  `workflow.node.*` span, so node spans cover the full unit of work.
- `eval.sinks.sqlite_results`: normalises `sqlite:///` URLs the same way the
  storage adapter does (shared `adapters.storage._url.path_from_sqlite_url`),
  so a `db_path` copied from `MANGOMAS_DB__URL` resolves to the intended file
  instead of creating a literal `sqlite:` directory; its `PersistenceError`
  detail is now truncated too.
- `eval.discovery`: acquires its tracer lazily (matching `agents.discovery`)
  instead of via the auto-configuring `mangomas.telemetry.get_tracer` at
  import time, and its idempotency latch is now a locked per-registry set
  instead of an unlocked global `bool`. Both discovery modules skip a
  non-callable entry-point factory with a warning instead of registering it.
- `eval.gate.merge_gate_results`: sources the merged verdict's threshold
  fields from the `kind="threshold"` verdict specifically, not positionally
  from the first argument — correct regardless of the order gates are passed
  in.
- `scripts/check_coverage.py`: the `api` coverage floor pattern was
  `src/mangomas/api/*.py` (non-recursive), so it silently excluded the
  `api/routes/` subpackage added in the M11 split; now `api/**/*.py`.
- `Makefile`'s `bridge-coverage` target used the default `.coverage` data
  file, so `make gate` (which runs `test → coverage → bridge-coverage`)
  overwrote the main suite's coverage data with the bridge's — a standalone
  `make coverage` run afterward would then measure the wrong run. Now uses
  `COVERAGE_FILE=.coverage.bridge` to keep the two isolated.

### Changed

- The inbound-header sanitiser shared by `correlation.py` and `tenancy.py`
  lives once in `mangomas._headers.sanitize_header_token` — the allowed-charset
  security invariant is stated in one place; both public APIs unchanged.
- Workflow node modules register through the new `register_node()` helper,
  stating each node kind literal exactly once.
- `api/app.py` decomposed into `api/errors.py`, `api/models.py`, and
  `api/routes/{system,agents,workflows}.py`; `create_app` is a slim assembly
  factory (the repo's last ruff C901 violation is gone) and the HTTP surface is
  byte-identical (OpenAPI schema diffed). Routers are built by factory
  functions inside `create_app` so per-app settings keep their construction-time
  semantics.
- `set_tenant` / `set_correlation_id` now return the ContextVar `Token`
  (additive) and are used by the tenancy/access-log middleware as the canonical
  setters; middleware docstring covers all four classes and bare status ints
  are `http.HTTPStatus` constants.
- `.github/workflows/ci.yml`'s lint/test/bridge-coverage jobs now invoke the
  corresponding `make` target instead of duplicating each command inline, so
  the two can no longer drift; a new `tests/deploy/test_ci_make_parity.py`
  locks the delegation and the global-floor/pytest-addopts equality in place.
- `Makefile` gains `gcp-secrets`, `gcp-trace`, and `langfuse` opt-in targets
  (marker-selected, since those tests live alongside their unit-test siblings
  rather than in a dedicated directory) — all nine `RUN_*`-gated suites are
  now reachable from `make help`.
- `scripts/check_coverage.py` gains floors for `_headers.py` (100%),
  `config.py`, `telemetry.py`, and `metrics.py` (95% each) — previously
  covered only by the blanket global floor.
- `.pre-commit-config.yaml` gains a local `lint-agent-frontmatter` hook, so a
  broken `.agent.md`/`SKILL.md` is caught before commit instead of only in CI.
- `chat`/`summarize`/`tool_agent`/`planner`/`reviewer` share one system-prompt
  resolution and message-building path via `agents._prompt.resolve_system_prompt`
  / `build_messages` (five previously-duplicated precedence ternaries and
  message-insert copies collapse to one; each agent's pre-existing precedence —
  settings-first vs. explicit-first — is preserved exactly, not unified).
  `PlannerAgent`/`ReviewerAgent` are now thin subclasses of the new
  `agents._structured.StructuredOutputAgent`. `AgentSettings.temperature` and
  `max_tokens` are live: every agent forwards them to `ctx.llm.complete()`/
  `stream()` instead of the settings existing but never being read.
- `eval`'s Langfuse sink/source share one client-bootstrap helper
  (`eval._langfuse`, ending 12 lines of verbatim duplication incl. an
  identical error string) and one option-validation helper module
  (`eval._options`: `require_str`/`require_list`/`require_unit_float`) instead
  of each implementing its own strict-vs-coercing validation. `AgentTarget`,
  `PipelineTarget`, `InlineSource`, and `JsonlSource` take keyword-only
  constructor arguments; entry-point iteration for eval plugin discovery moves
  to the shared `mangomas._entry_points`.
- LM Studio's chat-completion, streaming, embedding, and ping call sites share
  one POST/GET → `raise_for_status` → log → translate path
  (`OpenAICompatHTTPClient._request` / `_log_and_translate`) instead of
  repeating it four times across `adapters/llm/lmstudio.py` and
  `adapters/embeddings/lmstudio.py`.
- `secrets.gcp.GCPSecretManagerProvider.get()`'s five near-identical
  log-then-raise-if-strict failure branches collapse into one
  `_handle_failure()` helper; its lazy `secretmanager` import now raises
  `ImportError` with an install hint (`mangomas[gcp]`), matching every other
  lazy-SDK adapter.

### Added

- `MANGOMAS_AGENTS__<NAME>__MAX_TOOL_STEPS` per-agent setting
  (`AgentSettings.max_tool_steps`, default `None` → `DEFAULT_TOOL_MAX_STEPS=5`)
  replacing `ToolAgent`'s hard-coded step cap; resolution order is constructor
  arg > settings > default.
- `api.errors.error_envelope()` — the single construction site for the
  `{"error", "message"[, "detail"]}` body, shared by the exception handler and
  the middleware 413/503 rejections.
- `LLMClient.complete()` / `StreamingLLMClient.stream()` gain an additive
  keyword-only `max_tokens: int | None = None` parameter, implemented by the
  LM Studio and Vertex adapters (and `tests.fakes.FakeLLM`), so
  `AgentSettings.max_tokens` has somewhere to go.
- Claude Code ecosystem tooling (spec-0016 / ADR-0020): `.mcp.json` registers
  5 MCP servers (filesystem/git/fetch/sequential-thinking/repomix); `rtk`'s
  `PreToolUse` hook added to `.claude/settings.json`, additive and
  opt-out-gated via `MANGOMAS_DISABLE_RTK_HOOK`; `make validate-config` +
  `check-json` pre-commit hook guard both config files' JSON syntax;
  `docs/tooling/claude-code-ecosystem.md` catalogs all 7 evaluated tools,
  including the `claude-context` rejection. `claude-mem` was verified (via an
  isolated install under a throwaway `HOME`) to be a **user-scoped Claude Code
  plugin** that leaves the project's `.claude/settings.json` byte-identical —
  it takes no shared config and needs no opt-out gate, correcting spec-0016's
  original assumption. `claude-hud`'s final disposition remains pending in
  spec-0016.

### Removed

### Fixed — Docker build context excluded a file the Dockerfile copies

`deploy.yml` builds the production image with `docker build .`, the builder
stage runs `COPY pyproject.toml README.md ./`, and `pyproject.toml` declares
`readme = "README.md"` — but `.dockerignore` listed `README.md`, keeping it out
of the build context. The Cloud Run image build would fail at that `COPY`.

- **`.dockerignore`**: stop excluding `README.md` (with a comment recording
  why it must stay); additionally exclude `.hypothesis/`,
  `eval_harness_bridge/`, and `pyrightconfig.json`.
- **`tests/deploy/test_docker_build_context.py`**: new contract test that
  parses the `Dockerfile`'s `COPY` sources and asserts none is excluded by
  `.dockerignore`. Both files are parsed at runtime, so the test tracks the
  real manifests rather than a snapshot of them.

### Changed — Code hygiene: shared helpers, docs/config drift, tooling sync

Internal hygiene pass. No public contract changed; every adapter constructor
keeps its signature, and the new modules are private helpers behind the
existing protocols (the `adapters/_http_errors.py` precedent).

- **`adapters/_openai_client.py`** (new): `OpenAICompatHTTPClient` owns the
  httpx lifecycle both LM Studio adapters duplicated — base-URL normalisation,
  bearer-auth construction, injected-vs-owned client tracking, error-translation
  binding, and `aclose()`. Parameters past `model` are keyword-only and
  `_LABEL` / `_BAD_RESPONSE` are enforced by `__init_subclass__`, so a
  misconfigured subclass fails at import rather than from inside an error
  handler. Emits a debug log naming client ownership on close.
- **`adapters/embeddings/_shared.py`** (new): `SingleTextEmbedMixin` +
  `NoTransportAcloseMixin` replace byte-identical `embed()` / `aclose()` across
  all three embedding adapters — a new backend now implements only
  `embed_batch`.
- **`eval/_serialize.py`** (new): `report_payload()` is the single payload
  builder for the `json_file` and `webhook` sinks, replacing an invariant that
  was asserted only in a docstring. A round-trip test now binds it to
  `eval/baseline.py::load_baseline` in code.
- **`workflow/nodes/_factory.py`** (new): `make_node_factory()` collapses five
  identical hand-written node factories, removing five `# pragma: no cover`
  waivers in favour of per-kind tests of the type guard.
- **Docs drift**: `MANGOMAS_LLM__PROJECT` → `MANGOMAS_LLM__PROJECT_ID` and the
  nonexistent `MANGOMAS_LLM__MAX_OUTPUT_TOKENS` row removed (`CLAUDE.md`,
  `docs/architecture/cloud-providers.md`) — `Settings` uses `extra="ignore"`,
  so the documented names silently produced an unconfigured Vertex client.
  Corrected the `MANGOMAS_DB__URL` default, the `VertexClient` class name, and
  a `correlation.py` path that moved in v0.3.0.
- **Architecture docs**: C2/C3 now model the `workflow/` package, which was
  absent from both diagrams.
- **`Makefile`** (new): wraps each CI command as a target; `make gate`
  reproduces the full pipeline locally.
- **Coverage floors**: `scripts/check_coverage.py` is now stated as the single
  source of truth everywhere. CI dropped its weaker `--cov-fail-under=90`
  override (pyproject's 95 applies), and the docs no longer restate test counts
  that went stale every release.
- **Tooling**: pre-commit `ruff` v0.5.0 → v0.16.0 and `mypy` v1.10.0 → v2.3.0,
  both exact-pinned in the `dev` extra in lockstep with the hook revs;
  `pre-commit-hooks` v4.6.0 → v6.0.0; merged a duplicated mypy override block;
  dropped a no-op `T201` ignore. `PLR0917` is enabled with two narrow per-file
  ignores rather than globally suppressed.

### Removed

- **`config.DEFAULT_TOOL_MAX_STEPS`** — unreferenced; no `Settings` field
  backed it.
- **`eval/baseline.py::report_to_dict`** — unreferenced and absent from the
  package exports; callers use `eval/_serialize.py::report_payload`.
- **`scripts/run_pipeline_e2e.py`, `scripts/run_topologies_e2e.py`** — orphaned
  manual drivers superseded by `scripts/run_workflow_e2e.py`.
- **12 unused constants in `tests/constants.py`**; the remaining
  config-mirroring defaults are now re-exported from `mangomas.config` (via the
  explicit `X as X` idiom) instead of hand-restated, so they cannot desync.

### Added — Composite fan_out branches (workflow) + gated embedding smoke tests

Widen a `fan_out` branch to any `WorkflowStep` (spec 0013 / ADR-0018),
backwards-compatible (a superset).

- **`workflow/graph.py`**: `FanOutNode.branches` widens from `list[AgentNode]`
  to `list[WorkflowStep]` (an `agent` / `fan_out` / `loop` / `branch`).
- **`workflow/nodes/fan_out.py`**: hybrid executor — an **all-`agent`** fan_out
  still delegates to `dispatch_fan_out` verbatim (parity: identical output +
  spans); a composite branch runs via its executor under `asyncio.gather`. The
  `first`/`concat` join is unchanged.
- **Gated live embedding smoke tests** closing a coverage gap: `tests/lmstudio/
  test_embeddings.py` (`RUN_LMSTUDIO=1`) and `tests/vertex/test_embeddings.py`
  (`RUN_VERTEX=1`) exercise `LMStudioEmbeddingClient` / `VertexEmbeddingClient`
  against a real backend (skipped by default).

### Added — Multi-tenancy Phase 1 (storage isolation, opt-in)

Tenant-scoped conversation storage (spec 0007 / ADR-0017), additive and
default-OFF. Phase 2 (per-tenant `AgentSettings` at dispatch) is deferred.

- **`src/mangomas/tenancy.py`**: a `tenant_id` `ContextVar` + `set/get/resolve/
  sanitize_tenant` helpers, cloning the `correlation.py` pattern; `DEFAULT_TENANT`
  is the single source of the implicit `"default"` tenant.
- **`api/middleware.py`**: `TenancyMiddleware` sets the ContextVar from the
  configured header (installed only when `tenancy.enabled`).
- **Storage**: `adapters/storage/{sqlite,postgres}.py` add a
  `tenant TEXT NOT NULL DEFAULT 'default'` column with an idempotent migration
  for pre-tenancy databases, stamp `save_turn`, and filter `list_turns` by
  `WHERE tenant = ?`. The tenant is read from the ContextVar **inside** each
  method — so the `TurnRepository` Protocol signature is unchanged. Disabled ⇒
  all rows use `"default"` ⇒ byte-identical.
- **Config**: `MANGOMAS_TENANCY__ENABLED` (default `false`) / `__HEADER` / `__DEFAULT`.
- **Tests**: SQLite isolation + disabled-path parity + migration + a
  `TenancyMiddleware` end-to-end `/history` isolation test; gated Postgres
  isolation under `RUN_POSTGRES=1`. New `tenancy` coverage floor at 100%.

### Changed — Wave 1–3 hardening pass

Gap-analysis + hardening of the HTTP-surface work (no behaviour change by
default; all still additive/default-OFF):

- **CORS is fully env-driven** — `MANGOMAS_API__CORS_ALLOW_METHODS` /
  `__CORS_ALLOW_HEADERS` / `__CORS_ALLOW_CREDENTIALS` join `__CORS_ALLOW_ORIGINS`.
  Credentials default **off** (reflecting credentials with a wildcard origin is a
  browser-security footgun).
- **`/history` bounds are env-driven** — `MANGOMAS_API__HISTORY_DEFAULT_LIMIT` /
  `__HISTORY_MAX_LIMIT` replace the inline constants.
- **Single source for workflow opt-in precedence** — `resolve_workflow_source`
  moved to `workflow/loader.py` and shared by the CLI and HTTP surfaces (was
  duplicated).
- **Backpressure guards moved inner of the log/trace middlewares**, so a rejected
  413/503 still carries its `X-Request-ID` + access-log line; the at-capacity
  status constant/slug renamed to reflect its 503 (`server_at_capacity`).
- **Tests**: shared `_clear_settings_cache` fixture hoisted to `conftest.py`;
  the concurrency-wiring test now asserts the guard is actually installed; added
  negative tests for the auth validator and the metrics lifespan wiring.

### Added — Conditional workflow branch node

Add a `branch` node to the declarative workflow graph (spec 0012 / ADR-0016),
additive and default-OFF (existing graphs never carry `kind="branch"`).

- **`workflow/graph.py`**: frozen `BranchNode` (`kind="branch"`) — an ordered list
  of `{when: PredicateSpec, then: WorkflowStep}` cases plus an optional `default`
  — added to the `WorkflowStep` and `WorkflowNode` unions.
- **`workflow/nodes/branch.py`**: `BranchNodeExecutor` compiles each `when` once
  (reusing `compile_predicate`), evaluates them in order against the node's input
  content, and runs the first match's `then` via `resolve_executor` (so a branch
  child may itself be any node kind). No match + no `default` → `ConfigError`.
- Enables the `planner → route by output → specialised agent` pattern; the node
  selects one child and adds no back-edge, so the graph stays an acyclic tree.

### Added — HTTP surface parity (workflows, history, CORS)

Bring the FastAPI surface up to parity with the CLI, additive and default-OFF.

- **Workflow HTTP endpoint** (spec 0008 / ADR-0012): `POST /workflows/run` and
  `POST /workflows/validate` in `api/app.py`, delegating to the public
  `load_workflow` / `execute_workflow`. Graph-source resolution mirrors the CLI
  (`_resolve_workflow_source`): a per-request `definition` runs even when the
  feature is disabled, otherwise `workflow.enabled` + a configured definition is
  required. No new error type — reuses `ConfigError` (400) / `AgentNotFound`
  (404) / `MaxStepsExceeded` (422). No protected-path edits.
- **`GET /history`**: HTTP twin of `mangomas history`, delegating to
  `orch.context.repo.list_turns(limit=...)`; returns an empty list when no
  storage is configured.
- **Opt-in CORS**: `MANGOMAS_API__CORS_ALLOW_ORIGINS` (default empty →
  `CORSMiddleware` not installed, so behaviour is byte-identical unless set).

### Fixed

- **`cli/main.py`**: typed `_require_rag`'s return as
  `tuple[EmbeddingClient, VectorStoreRepository]` under `TYPE_CHECKING`, deleting
  the four `# type: ignore[arg-type]` comments (now redundant under
  `warn_unused_ignores`).
- **`CLAUDE.md`**: reconciled the stale "515 tests, 98.16 %" testing-conventions
  line with the real gate (`scripts/check_coverage.py` @ 95 % + per-package
  floors) and current counts.

### Added — Request backpressure (opt-in)

Bound request size and concurrency for a service fronting one slow upstream
(spec 0011 / ADR-0015), additive and default-OFF.

- **`api/middleware.py`**: `MaxBodySizeMiddleware` rejects a request whose
  `Content-Length` exceeds the limit with `413` before Starlette buffers it;
  `ConcurrencyLimitMiddleware` rejects requests beyond the in-flight cap with
  `503` (reject, don't queue) via a race-free in-flight counter.
- **Config**: `MANGOMAS_API__MAX_BODY_BYTES` / `MANGOMAS_API__MAX_CONCURRENT_REQUESTS`
  (both default `0` = off → the middleware is not installed).

### Added — Application authentication seam (opt-in)

Add a default-OFF bearer / API-key check on the data + execution routes
(spec 0010 / ADR-0014). Prerequisite for multi-tenancy.

- **`api/auth.py`**: a FastAPI dependency (`require_auth`) that resolves the
  expected token once (from `AuthSettings.secret_ref` via the `SecretsProvider`
  seam), compares it constant-time against `Authorization: Bearer` / `X-API-Key`,
  and **fails closed** if the secret does not resolve. Disabled → no-op.
- **Guarded routes**: `/agents/{name}/invoke|stream`, `/history`, `/workflows/*`.
  Probes (`/healthz`, `/readyz` + aliases) and `GET /agents` stay open.
- **`AuthenticationError`** (code `authentication_error`, HTTP 401) is an
  api-layer `MangomasError` subclass mapped in `_ERROR_STATUS`; `errors.py`
  (protected) is untouched.
- **Config**: `MANGOMAS_AUTH__ENABLED` (default `false`), `MANGOMAS_AUTH__SECRET_REF`
  (required when enabled).

### Added — OpenTelemetry metrics (opt-in)

Add a metrics pipeline alongside the existing span pipeline, additive and
default-OFF (spec 0009 / ADR-0013). Pays down the harness `METRICS_*` naming debt.

- **`telemetry.py`**: a `MeterProvider` behind `_build_metric_reader` (parallel to
  `_build_span_exporter`, reusing the `console`/`gcp` tokens), plus
  `configure_metrics` (idempotent, installs the provider only when enabled) and
  `get_meter`. Disabled by default → the global provider stays the OTel no-op, so
  recording is byte-identical.
- **`src/mangomas/metrics.py`**: record helpers for an agent-invocation counter
  (`agent`, `status`), an error counter (`agent`, `code`), and a duration
  histogram (`agent`); instruments bind lazily to the installed provider.
- **`api/app.py`**: emits those metrics at the `/agents/{name}/invoke` boundary
  (no protected-core edit); the lifespan calls `configure_metrics`.
- **Config**: `MANGOMAS_TELEMETRY__METRICS_ENABLED` (default `false`); reuses
  `MANGOMAS_TELEMETRY__EXPORTER` for the metric exporter.

### Added — Declarative multi-agent workflow graphs

Compose agents through a declarative JSON graph consumed by the `Orchestrator`,
additive and default-OFF (`MANGOMAS_WORKFLOW__ENABLED=false`). See spec 0005 and
ADR-0011.

- **`src/mangomas/workflow/`**: a pure-domain package (sibling of `rag/`/`eval/`)
  — a frozen-Pydantic `WorkflowGraph` (bounded tree: `sequence` of `agent` /
  `fan_out` / `loop`), a `node_registry` (mirrors `eval.target_registry`), a
  `PredicateSpec` → sync `AcceptanceFn` compiler, and `execute_workflow`. Every
  leaf is one public dispatch call; executors are metadata-transparent, so an
  all-agent `sequence` equals `dispatch_pipeline`.
- **`WorkflowSettings`** (`MANGOMAS_WORKFLOW__ENABLED` / `__DEFINITION`) and a
  `load_workflow` (path or inline JSON) loader; the graph's `schema_version` is
  validated at load. No new error types — reuses `ConfigError` (400) /
  `AgentNotFound` (404) / `MaxStepsExceeded` (422); `errors.py`,
  `core/*`, and `composition.py` are unchanged.
- **CLI**: `mangomas workflow run|validate` (off-by-default → exit 2).
- **Docs/harness**: `docs/workflow/graphs.md`, the `mango-workflow` skill, the
  `backend/workflow-graph-dev` sub-agent, a `workflow` per-package coverage floor,
  and `scripts/run_workflow_e2e.py`.

### Added — Cloud Run deploy pipeline (Milestone E)

Author-only deploy artifacts (ADR-0001, spec 0004). No GCP resources are
provisioned by this repo and no live deploy is validated by tests.

- **`deploy/service.yaml`**: Cloud Run (Knative serving v1) manifest — non-root,
  `$PORT`, liveness `/healthz` + readiness `/readyz`, secrets via
  `secretKeyRef` (never literals).
- **`.github/workflows/deploy.yml`**: on published release, build → Artifact
  Registry push → `gcloud run deploy`, authenticated via Workload Identity
  Federation (`id-token: write`; no service-account JSON keys).
- **`deploy/README.md`**: the full `MANGOMAS_*` runtime env-var contract.
- **`tests/deploy/`**: contract tests — manifest/workflow YAML validity, probe
  presence, WIF usage, and README doc-sync against `Settings.model_fields`.

### Added — Secrets strict mode (Milestone D)

Opt-in "fail loud" secret resolution (ADR-0010, amends ADR-002; spec 0003).
Additive and default-OFF.

- **`MANGOMAS_SECRETS__STRICT`** (default `false`): when `true`, cloud secrets
  backends raise the new **`SecretsResolutionError`** (HTTP 503) on
  auth/permission/timeout/API failures instead of returning `None`. `NotFound`
  still returns `None` — an absent secret is not a failure.
- `errors.py` gains `SecretsResolutionError` (`code="secrets_resolution_error"`,
  `.ref`/`.provider`); mapped to 503 in `api/app.py::_ERROR_STATUS`. The error
  carries only the short id + provider — never the value, path, or version.
- `secrets/gcp.py` honours `strict` via a single `_raise_if_strict` helper;
  `SecretsSettings.strict` wired through `composition.py`.

### Added — Telemetry exporter selection + harness routing (Milestone C)

Cloud Trace export and separate harness-span routing, both additive and
default-OFF (see ADR-0009, specs 0001/0002).

- **`MANGOMAS_TELEMETRY__EXPORTER`** (`console` default | `gcp`): new
  `TelemetrySettings` group selects the application span exporter behind the
  existing `configure_telemetry()`. `gcp` lazily imports the Cloud Trace
  exporter from the new `opentelemetry-exporter-gcp-trace` dependency under the
  `gcp` extra.
- **`MANGOMAS_HARNESS__METRICS_EXPORTER`** (`inherit` default | `console` |
  `gcp`): routes `harness.agent_invoke` spans to a dedicated `TracerProvider`
  when non-`inherit`; `inherit` reuses the global exporter (no change).
- `telemetry.py` gains `_build_span_exporter` (shared selector) and
  `build_scoped_tracer`; `_HarnessOrchestrator` uses the latter.
- New gated test marker `gcp_trace` (`RUN_GCP_TRACE=1`).

### Added — Dynamic agent loading via entry points (Milestone B)

Third-party packages can register agents into `agent_registry` without editing
`composition.py`. Additive and default-OFF (see ADR-0008, spec 0006).

- **`agents/discovery.py`**: `discover_agents` / `ensure_agent_plugins`,
  mirroring `eval/discovery.py` — entry-point group `mangomas.agents`, factory
  `Callable[[AgentSettings | None], Agent]`, once-per-process latch, log-and-skip
  on plugin failure. Gated by the existing `MANGOMAS_DISCOVERY_ENABLED` (no new
  env var).
- **`composition.py`**: `build_orchestrator` calls `ensure_agent_plugins` before
  the registration loop, so discovered agents are dispatchable with no wiring
  change.
- **Collision policy**: a discovered agent whose name collides with a **built-in**
  is skipped with a WARNING (never silently overrides `chat`/`planner`/etc.);
  third-party↔third-party keeps last-call-wins.
- `pyproject.toml` documents the `mangomas.agents` entry-point group.

### Added — Spec-driven workflow + Claude Code ecosystem refresh

Groundwork for the next-steps roadmap (see `specs/README.md`). Additive; no
runtime behaviour change.

- **`specs/` directory**: thin, non-CI-enforced spec-before-code convention with
  `specs/TEMPLATE.md`, `specs/README.md`, and long-term stubs
  `0005-declarative-agent-workflows`, `0006-dynamic-agent-loading`,
  `0007-multi-tenancy`.
- **New skills**: `mango-eval` (evaluation harness workflow) and `mango-deploy`
  (Cloud Run + telemetry-exporter workflow) under `.github/skills/`.
- **New sub-agent**: `telemetry-exporter-dev` under `backend`
  (`.github/agents/backend/`), owning the OTel exporter seam.

### Fixed — protected-path hook Windows bypass

- **`scripts/lint_agent_frontmatter.py`**: `_check_protected_path` now normalises
  the candidate path via the existing `_normalize_path` helper instead of
  `str.lstrip("./")`. Backslash paths (e.g. `src\mangomas\core\agent.py`)
  previously failed to match `PROTECTED_PATHS` and silently bypassed the hook on
  Windows; `lstrip` also stripped individual leading characters rather than a
  fixed prefix. The approval log now reports the marker actually matched (primary
  vs. legacy alias). Regression tests cover the backslash-normalisation path.

### Changed — protected-path hook + doc reconciliation

- **`scripts/lint_agent_frontmatter.py`**: `PROTECTED_PATHS` now covers all five
  documented core contracts — adds `core/orchestrator.py` and `core/tools.py`
  (alongside `core/agent.py`, `errors.py`, `registry.py`). The documented
  `BREAKING-CHANGE` marker is now the primary marker; the legacy
  `# approved-breaking-change` form is kept as an accepted alias. **This widens
  hook enforcement.**
- **Truncation constants**: the eval layer now reuses
  `config.DEFAULT_ERROR_DETAIL_TRUNCATE` for error-detail truncation instead of
  inline `[:200]` literals (`eval/dataset.py`, `eval/scorers/llm_judge.py`), and
  the two distinct-length truncations are named
  (`_MALFORMED_PREVIEW_TRUNCATE`, `_ROW_ERROR_TRUNCATE`). No behaviour change.
- **Docs**: `CLAUDE.md` protected-path list + File-Ownership table reconciled to
  the linter; `test-engineer` agent coverage-gate text corrected 85% → 95%;
  `NEXT_STEPS.md` sub-agent count corrected 12 → 13.
- **`EmbeddingScorer`** module docstring corrected — the scorer is operational
  against any `EmbeddingClient` via `ScorerContext.embeddings`; the
  `NotImplementedError` guard applies only when no embedder is configured.

### Added — Evaluation harness: gating, sinks, scorers, plugins

Adopts eval-harness patterns natively (see ADR-0003). All additions are
opt-in and default-OFF, so existing `mangomas eval` runs are unchanged.

- **Quality gate** (`eval/gate.py`): `evaluate_gate(report, ...) -> GateResult`.
  New `EvalSettings` fields `gate_enabled` / `min_mean_score` / `min_pass_rate` /
  `fail_on_error`. The CLI exits **3** when the gate fails (after sinks emit),
  distinct from 1 (runtime) and 2 (config).
- **Result sinks** (`eval/sink.py`, `eval/sink_registry.py`, `eval/sinks/`):
  `Sink` protocol + `sink_registry`. Built-ins `console` and `json_file` refactor
  the former inline CLI output; optional `langfuse` sink behind the new
  `mangomas[langfuse]` extra (lazy import, `LANGFUSE_*` env/ADC, mandatory
  `flush()`). Multiple sinks compose under per-sink fault isolation. `--output-json`
  is preserved by injecting `json_file`. New `sinks` / `sink_options` settings.
- **Scorers** (`eval/scorers/`): `regex_match`, `contains`, and `json_keys`
  (schema-conformance grading for structured agent output).
- **Plugin discovery** (`eval/discovery.py`): entry-point groups
  `mangomas.eval.scorers` / `mangomas.eval.sinks`, gated by
  `MANGOMAS_DISCOVERY_ENABLED`; failing plugins are logged and skipped.
- **Config version marker**: `EvalSettings.schema_version` (forward-compatible;
  a future version warns instead of crashing).

### Added — Evaluation harness: target indirection

Lets a run evaluate something other than a single registered agent (see
ADR-0004). Additive and default-OFF — `target` defaults to `agent`, so existing
`mangomas eval` / `EvalRunner.run(dataset, agent_name=...)` behaviour is
unchanged.

- **`Target` protocol + `target_registry`** (`eval/target.py`,
  `eval/target_registry.py`, `eval/targets/`): `async run(request, *, orch) -> str`.
  Built-ins `agent` (default, dispatches one agent), `pipeline`, `fan_out`
  (`join=first|concat`), and `echo` (deterministic baseline / test fixture).
- **`EvalRunner.run`** gains an optional keyword `target`; `agent_name` stays a
  positional and is wrapped in the default `agent` target. New additive
  `EvalReport.target_name` field (defaults to `""` for old artifacts).
- **CLI**: `mangomas eval --target <name>`; `--agent` folds into the `agent`
  target. New `EvalSettings.target` / `target_options`.
- **Plugin discovery**: new entry-point group `mangomas.eval.targets`
  (gated by `MANGOMAS_DISCOVERY_ENABLED`).

### Added — Evaluation harness: dataset source abstraction

Lets a dataset come from more than a local JSONL file (see ADR-0004). Additive
and default-OFF — `dataset_source` defaults to `jsonl`, so existing `--dataset`
runs are byte-for-byte unchanged.

- **`DatasetSource` protocol + `dataset_source_registry`**
  (`eval/dataset_source.py`, `eval/sources/`): `async load() -> list[DatasetRow]`.
  Built-ins `jsonl` (wraps `load_jsonl`), `inline` (rows via options, validated
  through the shared `_parse_row`), and optional `langfuse` (fetch a named
  dataset; `mangomas[langfuse]` extra, lazy import).
- **CLI**: `mangomas eval --dataset-source <name>`; `--dataset` feeds the
  `jsonl` source's `path`. New `EvalSettings.dataset_source` /
  `dataset_source_options`.
- **Plugin discovery**: new entry-point group `mangomas.eval.dataset_sources`.

### Added — Evaluation harness: SQLite + webhook sinks, per-row Langfuse

More result destinations, all additive and default-OFF (sinks default to
`["console"]`).

- **`SqliteResultsSink`** (`sqlite_results`): append the report + per-row
  results to `eval_reports` / `eval_rows` tables at `db_path` (queryable
  history; gate verdict stored as `gate_json`).
- **`WebhookSink`** (`webhook`): POST the `json_file`-shaped payload to `url`
  via httpx (core dep — no extra). Option `timeout_seconds`
  (`MANGOMAS_EVAL__WEBHOOK...` default 10s); non-2xx fails the sink.
- **Per-row Langfuse**: `LangfuseSink` gains a `per_row` option (default
  `false`) — when `true` it also emits one trace + `row_score` per row in
  addition to the aggregate trace + `mean_score`.

### Added — Evaluation harness: regression / baseline gating

Fail CI when a run regresses against a saved baseline (see ADR-0005). Additive
and default-OFF — engaged only when `--baseline` / `MANGOMAS_EVAL__BASELINE_PATH`
is set.

- **`eval/baseline.py`**: `load_baseline` (reconstructs an `EvalReport` from a
  `json_file` artifact, ignoring the `"gate"` key and tolerating a missing
  `target_name`); pure `diff_reports(baseline, current) -> ReportDiff`
  (per-metric deltas + regressed/new/dropped row partition).
- **`eval/gate.py`**: `evaluate_regression_gate(diff, *, max_mean_score_drop,
  max_pass_rate_drop, allow_new_failures) -> GateResult` (reuses `GateResult`);
  `merge_gate_results` combines threshold + regression verdicts (AND).
- **CLI**: `--baseline`, `--max-mean-score-drop`, `--max-pass-rate-drop`,
  `--allow-new-failures/--no-allow-new-failures`; a missing baseline is exit 2.
  New `EvalSettings.baseline_path` / `max_mean_score_drop` / `max_pass_rate_drop`
  / `allow_new_failures`.

### Added — Retrieval-augmented generation (RAG)

The full RAG port lands as a non-breaking, opt-in layer. Embeddings and the
vector store are both gated `enabled=False` by default, so existing
deployments and the test suite see no behaviour change. This completes the
shipped-but-stubbed `EmbeddingScorer` (it raised `NotImplementedError` because
no provider exposed `.embed()`) and gives agents retrieval context via a
`RetrievalTool` auto-discovered through the existing `ToolAgent`.

- **`EmbeddingClient` seam** (`adapters/embeddings/base.py`,
  `@runtime_checkable`): `embed` / `embed_batch` / `aclose`. Three backends —
  `LMStudioEmbeddingClient` (httpx POST `{base_url}/embeddings`),
  `SentenceTransformersEmbeddingClient` (in-process, lazy SDK, off-thread
  `encode`), and `VertexEmbeddingClient` (`text-embedding-004`, **ADC only —
  no service-account-JSON path**). All three delegate `embed` to
  `embed_batch([text])[0]`; `list[float]` everywhere (no numpy).
- **`VectorStoreRepository` seam** (`adapters/vector/base.py`): primitives only
  (`upsert` / `query` / `delete_by_source` / `aclose` + `VectorMatch`), so the
  vector layer never imports `rag/`. `ChromaVectorStore` forces
  `metadata={"hnsw:space": "cosine"}` and maps cosine distance → similarity as
  `1 - distance / 2` (`_MAX_COSINE_DISTANCE`), keeping scores in `[0, 1]` — a
  plain `1 - distance` would go negative in Chroma's default L2 space.
- **`rag/` domain package**: `chunk_text` word-window chunker (pure fn),
  `load_documents` (`*.md`/`*.txt`, off-thread), `IngestionPipeline`
  (`delete_by_source` → chunk → `embed_batch` in `batch_size` slices →
  `upsert`, with stable `{source}#{index}` ids so re-ingest leaves no orphan
  chunks), and `Retriever` + `RetrievalTool` (satisfies the `Tool` protocol).
- **Shared adapter error helpers** (`adapters/_http_errors.py`,
  `adapters/_vertex_errors.py`): the httpx → typed-error translation and the
  Vertex qualname error matrix are now single reusable modules consumed by both
  the chat and embedding adapters, removing cross-adapter private imports and a
  duplicated `[:200]` literal (now `DEFAULT_ERROR_DETAIL_TRUNCATE`).
- **Config**: `EmbeddingSettings` (`MANGOMAS_EMBEDDINGS__*`), `VectorSettings`
  (`MANGOMAS_VECTOR__*`), `RagSettings` (`MANGOMAS_RAG__*`) with `DEFAULT_*`
  constants. `RagSettings` validates `1 <= chunk_words`,
  `0 <= chunk_overlap < chunk_words`, `0 <= min_chunk_words` at construction so
  a bad env value fails fast rather than deep in the pipeline.
- **Wiring**: `AgentContext.embeddings` / `AgentContext.vector_store` fields
  (default `None`, TYPE_CHECKING imports); `embedding_registry` +
  `_vector_registry` in `composition.py`; `Orchestrator.aclose()` closes both
  new components (fault-tolerant, idempotent) so the LM Studio httpx client and
  Chroma client never leak per CLI run. When both are present, a `Retriever` +
  `RetrievalTool` is registered into `ctx.tools` for `ToolAgent` auto-discovery.
- **CLI**: `mangomas rag ingest <path>` and `mangomas rag query <text>` (both
  exit `2` with a clear message when RAG is disabled).
- **Eval**: `ScorerContext.embeddings`; `EmbeddingScorer` now resolves a real
  provider (falls back to `context.llm` when it exposes `.embed()`), only
  raising `NotImplementedError` when neither is available.
- **Packaging / tests**: `embeddings-local` (sentence-transformers) and `rag`
  (chromadb) optional extras; Vertex embeddings reuse the `vertex` extra.
  `embeddings_local` / `rag` pytest markers + `RUN_EMBEDDINGS_LOCAL` / `RUN_RAG`
  gates. New unit suites under `tests/adapters/` and `tests/rag/`, CLI tests in
  `tests/test_cli_rag.py`, and a new **95 %** `rag` per-package coverage floor in
  `scripts/check_coverage.py` (adapters caught by the existing 85 % floor).
- **Docs**: `mango-rag` skill (`.github/skills/mango-rag/SKILL.md`); CLAUDE.md,
  README, and C4 component/container diagrams document the embeddings/vector/rag
  seams; NEXT_STEPS graduates the "embedding-capable provider" long-term item.

### Added — Claude Code enterprise harness

The first end-to-end Claude Code harness lands as a non-breaking,
opt-in layer on top of v0.3.0. Production callers behave identically
unless `MANGOMAS_HARNESS__ENABLED=true` is set; every artifact obeys
the project's no-hard-coded-values and protocol-first rules.

- **Skill files** (`.github/skills/<name>/SKILL.md`): seven workflow
  skills — `mango-adapter`, `mango-agent-add`, `mango-config`,
  `mango-error`, `mango-observability`, `mango-release`,
  `mango-topology`, plus the pre-existing `mango-testing`. Each ships
  the canonical step-by-step workflow for the area it covers.
- **Sub-agents** (`.github/agents/<parent>/<slug>.agent.md`): twelve
  specialised sub-agents grouped under the four parent agents
  (`architect`, `backend`, `test-engineer`, `api-dev`). The parent
  agents now declare an optional `sub_agents:` frontmatter list. The
  key is backwards-compatible — parents without it remain valid.
- **`HarnessSettings`** in `src/mangomas/config.py` (env prefix
  `MANGOMAS_HARNESS__`): `enabled` (bool, default `False`),
  `metrics_namespace` (str, default `"mangomas.harness"`),
  `hook_log_level` (Literal `DEBUG|INFO|WARNING`, default `"INFO"`).
  Defaults are inert so existing wiring is unchanged.
- **`_HarnessOrchestrator`** in `composition.py`: `Orchestrator`
  subclass engaged only when `harness.enabled=True`. Wraps `dispatch`
  *and* `stream_dispatch` in a single `harness.agent_invoke` parent
  span tagged with `agent.name`, `harness.topology`
  (`dispatch`/`stream`), and `messages.count`. `dispatch_pipeline` and
  `dispatch_fan_out` inherit the wrap automatically because they
  delegate through `dispatch`.
- **`scripts/lint_agent_frontmatter.py`**: Pydantic-driven validator
  for `*.agent.md` / `SKILL.md` frontmatter. Validates required keys,
  description length, allowed `tools` values, and resolves the new
  `sub_agents:` slugs against `<parent>/<slug>.agent.md`. Also
  enforces a "BREAKING-CHANGE" marker on staged diffs that touch a
  protected core path (`src/mangomas/core/agent.py`,
  `src/mangomas/registry.py`, `src/mangomas/core/orchestrator.py`,
  `src/mangomas/core/tools.py`). Exits `0/1/2` for ok/schema/protected.
  Wired into CI via a new `frontmatter-lint` job in
  `.github/workflows/ci.yml`.
- **`scripts/harness_session_start.py`**: `SessionStart` hook for
  Claude Code on the web. Emits a single-line JSON probe report
  covering venv presence and LM Studio reachability so a fresh session
  knows immediately what's available. Honours
  `MANGOMAS_HARNESS__HOOK_LOG_LEVEL`. Always returns `EXIT_OK` so a
  failed probe never blocks a session.
- **`tests/_script_loader.py`**: shared helper for importing
  `scripts/*.py` modules in tests via `load_script_module(name)`.
  Centralises the `importlib.util.spec_from_file_location`
  boilerplate that the two script-under-test files previously
  duplicated.
- **`.claude/settings.json`**: project-scoped harness configuration —
  pinned `allow`/`deny` permissions, `MANGOMAS_LOG__FORMAT=json` env,
  and three hooks: `SessionStart`, `PreToolUse` (Bash gating),
  `PostToolUse` (`ruff --fix` on Edit/Write), `Stop` (silent coverage
  re-run).
- **PR automation**: `.github/PULL_REQUEST_TEMPLATE.md` enforces
  CHANGELOG entry, ADR linkage, and the breaking-change marker;
  `docs/adr/_template.md` for new ADRs; `secret-scan` Gitleaks job
  added to `.github/workflows/ci.yml`.
- **C4 diagrams**: `docs/architecture/c2-container.md` and
  `c3-component.md` now describe the harness layer (skills,
  sub-agents, `_HarnessOrchestrator`, frontmatter linter,
  SessionStart hook) and indicate which boxes are dormant when
  `harness.enabled=False`.
- **Regression suite**: composition coverage rises 90 % → 100 % via
  six new cases (`_HarnessOrchestrator.dispatch` /
  `stream_dispatch`, `_file_memory_factory`,
  `memory.enabled=True` branch, frontmatter linter
  `EXIT_SCHEMA` branch, `_staged_diff` git-failure branch). Total
  global coverage 96.95 %, 505 unit tests pass after the v0.3.0
  reconciliation (up from 354 pre-merge).
- **`.gitignore` / `.dockerignore`** now exclude harness scratch
  state (`.claude/settings.local.json`, `.claude/cache/`,
  `.claude/state/`, `.claude/logs/`) and the harness scripts from
  the runtime container image (they are development tooling).

### Changed

- `CLAUDE.md` documents the harness model: skills table, sub-agents
  table, the `sub_agents:` key, and how `HarnessSettings` engages
  `_HarnessOrchestrator`.
- `NEXT_STEPS.md` graduates the harness milestone and frames the
  next iteration (entry-point agent discovery, harness-level metrics
  exporter selection).
- `README.md` adds a short "Claude Code harness" section pointing at
  `.github/agents/` + `.github/skills/` and explaining the opt-in
  switch.

### Backwards-compatibility

- `HarnessSettings.enabled` defaults to `False`. With the default,
  `build_orchestrator` returns a vanilla `Orchestrator`, the
  composition tests for the old shape still pass, and the unmodified
  CLI/API surface is unchanged.
- The optional `sub_agents:` frontmatter key is rejected on child
  files (hierarchy is two-deep only) but absent-or-empty on parent
  files is valid.

<!-- next release goes above this line -->

## [0.3.1] — 2026-05-23

### Added

- **GCP swap-in implementation plan** (`docs/plans/20260523T133844Z-gcp-swapin-and-evals-plan.md`):
  Cherry-picked from PR #6 — 7-milestone roadmap covering Cloud Logging/Trace
  exporter, Vertex AI provider hardening, Postgres parity, Cloud Run deployment,
  and evaluation harness enhancements. Destructive code deletions in PR #6 were
  rejected; only the plan document was merged.
- **10 new mocked asyncpg unit tests** in `tests/test_postgres.py`:
  `save_turn` / `list_turns` happy path + error translation, `aclose` / `close`
  with injected pool, empty results, null timestamp handling. Postgres module
  coverage 51 % → 80 %.

### Fixed

- **ruff PLR2004** in `scripts/lint_agent_frontmatter.py`: extracted magic
  number `3` to named constant `_MIN_SUBAGENT_PATH_DEPTH`.
- **mypy `no-any-return`** in `src/mangomas/secrets/gcp.py`: replaced raw
  `return self._client` with `cast("secretmanager.SecretManagerServiceClient",
  self._client)` so the return type annotation is satisfied without a blanket
  `type: ignore`.

### Changed

- Global test count 505 → 515; global coverage 96.95 % → 98.16 %.
- `.gitignore` now excludes `.gemini/` workspace artifacts and stale
  `docs/antigravity_reference.md`.

### Removed

- Stale `docs/antigravity_reference.md` (Antigravity workspace config that
  should never have been committed).

## [0.3.0] — 2026-05-23

The full GCP swap matrix from ADR-001 closes in v0.3.0: Vertex AI LLM,
Cloud SQL Postgres storage, and GCP Secret Manager all ship behind the
existing registry + protocol seams alongside the long-term offline
evaluation harness. No core changes; every provider is selectable via
`Settings`. Identity throughout is Application Default Credentials /
Workload Identity Federation only — service-account JSON keys are never
accepted by code or configuration.

### Added

- **Vertex AI LLM provider** (`vertex` extra,
  `src/mangomas/adapters/llm/vertex.py`). `VertexClient` satisfies
  `LLMClient`, `PingableLLMClient`, and `StreamingLLMClient` via
  `vertexai.generative_models.GenerativeModel`. SDK imports are
  deferred to `VertexClient.__init__` so the module is always importable
  even without the extra installed; the class then raises a clear
  `ImportError` pointing at `pip install 'mangomas[vertex]'`.
  Registered by `_vertex_factory` in `composition.py` and selected via
  `MANGOMAS_LLM__PROVIDER=vertex`. Qualname-based error translation maps
  `google.api_core.exceptions.*` and `google.auth.exceptions.*` to typed
  `LLMTimeout` / `LLMUnavailable` / `VertexError(LLMBadResponse)`. New
  `LLMSettings` fields: `project_id`, `location`, `credentials_path`.
  The existing `secret_ref` flow is reused — the resolved value is
  forwarded to the factory as `credentials_json` (service-account JSON
  body). `vertex` pytest marker + `RUN_VERTEX=1` gating in
  `tests/vertex/` (smoke / chat invoke / chat stream / unknown model).
  See `docs/adapters/vertex.md`.
- **Cloud SQL / Postgres storage provider** (`postgres` extra,
  `src/mangomas/adapters/storage/postgres.py`). `PostgresRepository`
  satisfies `TurnRepository` and the new `AsyncCloseableRepository`
  extension. Backed by `asyncpg` with a connection pool — no
  `threading.Lock` (native async). Lazy pool creation preserves the
  existing `_sqlite_factory(cfg: DBSettings) -> SQLiteRepository`
  factory shape. A JSONB codec is registered on every connection so
  `list_turns` returns dicts (matching SQLite's row shape). Activate
  via `MANGOMAS_DB__PROVIDER=postgres` +
  `MANGOMAS_DB__URL=postgresql://...`. testcontainers-driven
  integration suite under `tests/postgres/` gated on `RUN_POSTGRES=1`
  (`smoke`, `persistence`, 50-way fan-out `concurrency`). See
  `docs/testing/postgres-integration.md`.
- **GCP Secret Manager provider** (`gcp` extra,
  `src/mangomas/secrets/gcp.py`). `GCPSecretManagerProvider` satisfies
  `SecretsProvider`. Supports both short ids (resolved against
  `MANGOMAS_SECRETS__PROJECT_ID` + `MANGOMAS_SECRETS__DEFAULT_VERSION`)
  and full `projects/.../secrets/.../versions/...` resource paths. All
  failure modes collapse to `None` per ADR-002 so
  `_resolve_llm_secrets` continues to fall back to the inline `api_key`
  in local dev. Activate via `MANGOMAS_SECRETS__PROVIDER=gcp` +
  `MANGOMAS_SECRETS__PROJECT_ID=...`. Lazily registered inside
  `build_orchestrator` (the secrets registry stores instances, not
  factories, so config-bound construction has to happen there).
- **Offline evaluation harness** (`src/mangomas/eval/`). New
  `Scorer` protocol, `scorer_registry`, JSONL `load_jsonl`, `EvalRunner`
  that reuses the existing `Orchestrator`, `EvalReport` aggregator, and
  three built-in scorers: `ExactMatchScorer`, `LLMJudgeScorer`,
  `EmbeddingScorer` (latter raises `NotImplementedError` until a
  provider exposes `.embed()`). `EvalSettings` block (env prefix
  `MANGOMAS_EVAL__`). `mangomas eval` CLI subcommand reads defaults
  from `EvalSettings`; `--output-json` writes a structured report. See
  `docs/eval/harness.md`.
- **`AsyncCloseableRepository` extension protocol**
  (`src/mangomas/adapters/storage/base.py`): adapters with async-pool
  teardown expose `aclose()`; the FastAPI lifespan and CLI close path
  dispatch on its presence. SQLite continues to satisfy the bare
  `TurnRepository` protocol unchanged.
- **CLI close path**: `agents`, `chat`, and `history` commands now
  wrap their work in `try/finally: asyncio.run(_close_orchestrator(orch))`
  so asyncpg pools don't leak at CLI process exit.
- **`tests/test_sqlite_concurrency.py`**: 50-way `asyncio.gather`
  fan-out of `save_turn` against in-memory SQLite. Closes the gap
  from the v0.1.0 `threading.Lock` fix that previously had no direct
  regression test. Runs in the default suite.
- **`tests/postgres/test_concurrency.py`**: symmetric 50-way fan-out
  test against `PostgresRepository` — pins the asyncpg pool's
  concurrent-write contract. Gated on `RUN_POSTGRES=1`.
- **ADR-002** (`docs/adr/0002-secrets-provider-error-semantics.md`):
  records the choice to collapse cloud-secrets backend failures into
  `None` rather than raise, with a v0.4.0 follow-up for a
  `SecretsSettings.strict` opt-in.
- **`docs/architecture/cloud-providers.md`**: single combined page
  covering Vertex / Postgres / GCP Secret Manager — env-var contracts,
  registration cites, ambient-identity guidance, and the rule-of-three
  rationale for not extracting a shared lazy-SDK base class yet.
- **`docs/adapters/vertex.md`** and **`docs/eval/harness.md`**: dedicated
  per-feature usage docs for the Vertex adapter and the evaluation
  harness.
- **New optional extras** in `pyproject.toml`: `vertex`, `postgres`,
  `gcp`, and meta-extra `cloud`. `dev` adds `asyncpg` and
  `testcontainers` so Postgres unit tests and the testcontainer suite
  collect cleanly; the Vertex and GCP SDKs stay out of `dev` because
  unit tests mock at the constructor boundary.
- **New pytest markers**: `postgres`, `vertex`, `gcp_secrets` and
  corresponding `RUN_*` env-var gates in
  `tests/conftest.py::pytest_collection_modifyitems`.
- **Postgres compose profile** in `docker-compose.yml`: opt-in via
  `docker compose --profile postgres up -d postgres`; credentials
  local-only.
- **`DEFAULT_ERROR_DETAIL_TRUNCATE`** constant in `mangomas.config`
  replaces inline `[:200]` literals across the cloud adapters.

### Changed

- `LLMSettings` gains optional `project_id`, `location`,
  `credentials_path` for Vertex (defaults preserve LM Studio behaviour).
- `DBSettings` gains optional `pool_min`, `pool_max`,
  `connect_timeout_seconds`, `statement_timeout_seconds` for Postgres
  (defaults preserve SQLite behaviour).
- `SecretsSettings` gains optional `project_id`, `timeout_seconds`,
  `default_version` for GCP (defaults preserve env-backend behaviour).
- FastAPI lifespan and CLI close path dispatch on
  `hasattr(repo, "aclose")` — backwards-compatible with
  `SQLiteRepository`'s sync `close()`.
- `build_orchestrator` log line gains a `secrets_provider` field.
- **Per-package coverage floors raised** in `scripts/check_coverage.py`
  to match the post-v0.3.0 actuals: `composition` 90 → 95, `api`
  90 → 95, `cli` 90 → 95, global 90 → 95. New `eval` floor at 95 %.
  `agents` (95 %) and `adapters` (85 %) unchanged. `pyproject.toml`
  global `--cov-fail-under=90` → `95`.
- **Magic-number cleanup in LLM adapters**: `LMStudioClient` and
  `VertexClient` constructors now reference `DEFAULT_LLM_TIMEOUT_SECONDS`
  and `DEFAULT_LLM_TEMPERATURE` from `mangomas.config` instead of inline
  literals. The SSE `[DONE]` sentinel and the Vertex ping prompt are
  named module-level `Final` constants.
- **Test-side magic literal cleanup**: `tests/test_lmstudio.py` consumes
  new `TEST_LMSTUDIO_MOCK_BASE_URL` / `TEST_LMSTUDIO_MOCK_MODEL`
  constants; `tests/test_correlation.py`, `tests/integration/test_api_flow.py`
  consume `ASGI_TEST_BASE_URL`; `tests/test_api.py`, `tests/test_cli.py`,
  `tests/eval/test_dataset.py` consume `STUB_REPLY`; `tests/test_sqlite.py`
  consumes `DEFAULT_AGENT_NAME`.
- **`tests/conftest.py`** no longer re-exports `Fake*` from
  `tests.fakes`. The remaining importer (`tests/test_agent.py`) now
  imports from the canonical `tests.fakes` path. Mirrors the
  `mangomas.api.correlation` shim removal.
- **Stale docstring** in `secrets/provider.py` referring to v0.2.0
  updated to a version-agnostic statement.
- `pyproject.toml`: version bumped to `0.3.0`; mypy
  `ignore_missing_imports` extended to cover `google.*`, `vertexai.*`,
  `asyncpg.*`, `testcontainers.*`. `tests/*` per-file ruff ignore
  extends to `SLF001` so tests can introspect adapter internals.

### Removed

- **`mangomas.api.correlation` shim** deleted. The canonical home is
  and has always been `mangomas.correlation`. The shim shipped in v0.2.0
  as a short-term migration aid; with no external consumers (project is
  pre-1.0) the duplicate import path is now retired. Update imports to
  `from mangomas.correlation import ...`.
- **`Settings.discovery_enabled`** field removed. Defined in v0.1.0 as
  a placeholder for entry-point-based agent discovery; no factory ever
  read it. The feature itself remains tracked under `NEXT_STEPS.md`
  "Long term" and will re-introduce a field alongside the real
  implementation if and when it lands.

### Security / Operations

- All cloud adapters consume **ambient identity only** (ADC / Workload
  Identity Federation). Service-account JSON keys are not accepted by
  configuration, env, or code (the Vertex provider's `credentials_json`
  is sourced exclusively from the `SecretsProvider` seam — never from a
  direct env variable).
- Secret values are **never** logged. Cloud secret resource paths are
  truncated to the short id in `extra={}` log fields. Postgres DSNs
  are **never** logged in full; only the parsed host appears.
- ADR-002 documents that rotated GCP secrets can silently degrade to
  an inline `api_key`; operators MUST alert on
  `logger=mangomas.secrets.gcp severity=ERROR`.

## [0.2.0] — 2026-05-13

### Added

- **Harness gap-analysis sweep**:
  - `_HarnessOrchestrator` now also wraps `stream_dispatch` so streaming
    invocations get the same `harness.agent_invoke` parent span as
    non-streaming dispatch. Adds `messages.count` and `harness.topology`
    span attributes on both wraps. New `_HARNESS_SPAN_NAME`,
    `_HARNESS_TOPOLOGY_DISPATCH`, `_HARNESS_TOPOLOGY_STREAM` module
    constants — no inline literals.
  - `_HarnessOrchestrator.__init__` and both dispatch wraps emit
    `logger.debug` lines so the wrap is observable without enabling DEBUG
    everywhere.
  - Removed the unused `ALLOWED_TOOLS` constant from
    `scripts/lint_agent_frontmatter.py` — the `Literal` annotation on
    `AgentFrontmatter.tools` is the live source of truth, no parallel
    constant needed.
  - New `tests/_script_loader.py` shared helper centralises the
    `importlib.util.spec_from_file_location` boilerplate that the two
    script-under-test files previously duplicated. Both test files now
    import from it.
  - `tests/test_lint_agent_frontmatter.py` and
    `tests/test_harness_session_start.py` now reference symbolic constants
    (`linter.PROTECTED_PATHS`, `constants.DEFAULT_LLM_BASE_URL`) instead
    of inline strings — easier to refactor.
  - Coverage backfill: `_HarnessOrchestrator.dispatch` /
    `stream_dispatch`, `_file_memory_factory`, the
    `memory.enabled=True` branch in `build_orchestrator`, and the git-
    failure branch in `_staged_diff` are now covered. Composition
    coverage 90 % → 100 %; global 98.55 % → 99.11 %. Total tests
    354 → 360.
- **Claude Code PR automation** (Phase 4):
  - `.github/PULL_REQUEST_TEMPLATE.md` with Summary, Changes, Test-plan
    checklist (ruff/mypy/pytest/coverage/frontmatter-lint/manual-smoke),
    ADR link, CHANGELOG link, and per-parent sub-agent review boxes.
  - `docs/adr/_template.md` — copyable ADR skeleton (Status / Context /
    Decision / Consequences / Alternatives / References). The previous
    inline copy in `.github/agents/architect.agent.md` is trimmed to a
    one-line pointer at the template.
  - `pr-watcher` sub-agent under architect — documents the canonical
    `subscribe_pr_activity` lifecycle (subscribe on open, triage events
    by type, push only when confident, escalate via `AskUserQuestion`
    when ambiguous, unsubscribe on close/merge). Declared in the
    `architect.agent.md` `sub_agents:` list, bringing the total to
    13 sub-agents.
  - `CLAUDE.md` sub-agent table updated to include `pr-watcher`;
    `.github/copilot-instructions.md` gains a "PR Workflow" subsection.
- **CI secret-scan fix**: switched the `secret-scan` job from
  `gitleaks/gitleaks-action@v2` (which requires a paid license for org
  accounts) to a direct `curl`+`tar` install of the open-source
  `gitleaks` v8.21.2 binary. Same scan, no license requirement.
- **Claude Code harness enforcement layer** (Phase 3):
  - `.claude/settings.json` with a permissions allowlist for the standard
    test / lint / type-check / coverage / git read-only / gh read-only
    commands; explicit deny for `rm -rf` and `git push --force`. Hooks:
    SessionStart runs `scripts/harness_session_start.py` (warns on missing
    `.venv` and unreachable LM Studio, never fails); PreToolUse on
    Edit/Write runs `scripts/lint_agent_frontmatter.py
    --check-protected-paths` against `src/mangomas/core/agent.py`,
    `errors.py`, and `registry.py`, blocking edits that lack the
    `# approved-breaking-change` marker; PostToolUse runs `ruff
    check --fix` on the touched file; Stop runs `pytest --cov-fail-under=85
    -q` before declaring done. All hook commands are best-effort
    (`|| true`) so a failure never strands a session.
  - `scripts/lint_agent_frontmatter.py` — Pydantic v2-validated linter for
    every `.github/agents/**/*.agent.md` and `.github/skills/**/SKILL.md`.
    Resolves `sub_agents:` slugs against on-disk child files; supports
    `--check-protected-paths` mode for the PreToolUse hook. Module
    constants (`AGENTS_GLOB`, `SKILLS_GLOB`, `PROTECTED_PATHS`,
    `BREAKING_CHANGE_MARKER`, `EXIT_OK`/`EXIT_SCHEMA`/`EXIT_PROTECTED`)
    keep magic literals out of the body.
  - `scripts/harness_session_start.py` — SessionStart hook. Reuses
    `mangomas.telemetry.configure_telemetry` and the existing httpx
    dependency; emits structured logs in the
    `MANGOMAS_HARNESS__METRICS_NAMESPACE` namespace.
  - `HarnessSettings` group in `mangomas.config` (Pydantic v2 `BaseModel`
    + module-level `DEFAULT_HARNESS_*` constants) with `enabled`,
    `metrics_namespace`, `hook_log_level` fields. Defaults are
    backward-compatible (`enabled=False`); env overrides via
    `MANGOMAS_HARNESS__*`.
  - `_HarnessOrchestrator` in `mangomas.composition` — additive
    `Orchestrator` subclass that wraps `dispatch` in a
    `harness.agent_invoke` parent span. Engaged only when
    `cfg.harness.enabled` is `True`; zero overhead and zero behaviour
    change otherwise (existing `orchestrator.dispatch` spans nest
    underneath).
  - CI: new `Frontmatter lint` step in the `lint` job and a new
    `secret-scan` job using `gitleaks/gitleaks-action@v2`.
  - Dev deps: `pyyaml>=6.0`, `types-PyYAML>=6.0` (consumed by the
    frontmatter linter).
  - Tests: `tests/test_lint_agent_frontmatter.py` (15 cases covering
    schema, `sub_agents` resolution, and the protected-path hook),
    `tests/test_harness_settings.py` (defaults + env overrides),
    `tests/test_harness_session_start.py` (venv detection + LM Studio
    probe success/failure paths), and 2 new `test_composition.py` cases
    that confirm the wrapper engages only under `harness.enabled=True`.
    Total +30 cases; per-package coverage floors all hold.
- **Claude Code sub-agents (12 new files)** under `.github/agents/<parent>/`:
  architect → `protocol-auditor`, `layering-auditor`, `adr-author`; backend →
  `llm-adapter-dev`, `storage-adapter-dev`, `orchestrator-dev`,
  `error-taxonomy-dev`; test-engineer → `fake-builder`, `hypothesis-fuzz`,
  `integration-runner`; api-dev → `sse-streamer`, `schema-evolution`. Parent
  agents declare children via a new optional `sub_agents:` frontmatter list;
  the four existing parents (`api-dev`, `architect`, `backend`,
  `test-engineer`) gained this list and remain backward-compatible. `CLAUDE.md`
  and `.github/copilot-instructions.md` document the convention.
- **Claude Code skill library (7 new skills)** under `.github/skills/`:
  `mango-adapter`, `mango-agent-add`, `mango-error`, `mango-observability`,
  `mango-config`, `mango-topology`, `mango-release`. Each codifies an
  existing convention in `CLAUDE.md` (Protocol-first adapters, the 4-step
  agent extension pattern, the `errors.py`/`_ERROR_STATUS`/`test_errors.py`
  lock-step, the `get_tracer` + structured-logging contract, the
  `MANGOMAS_*` env prefix + `DEFAULT_*` constants pattern, the
  `dispatch_pipeline`/`dispatch_fan_out`/`stream_dispatch` topology
  surface, and the conventional-commit + CHANGELOG release flow).
  Modelled exactly on the existing `mango-testing/SKILL.md` frontmatter
  schema (`name`, multiline `description`, `argument-hint`). No source
  changes; documentation only.
- **LM Studio E2E scenarios 2–6** under `tests/lmstudio/`: chat invoke happy path,
  chat stream SSE (token + done frames), buffered-fallback warning via
  `Registry.scoped()`, summarize agent through the public API, and the
  unknown-model 502 error envelope. All are `@pytest.mark.lmstudio` and gated
  on `RUN_LMSTUDIO=1`.
- **Streaming support on `PlannerAgent` and `ReviewerAgent`** via an async
  `stream()` method that mirrors `ChatAgent._do_stream`'s buffered-fallback
  pattern. Both now satisfy the `StreamingAgent` protocol so
  `/agents/{name}/stream` delivers tokens incrementally with no orchestrator
  changes.
- **Per-request correlation IDs** end-to-end. New
  `src/mangomas/api/correlation.py` exposes a `ContextVar` and a
  `CorrelationFilter` for log records. `AccessLogMiddleware` reads
  `X-Request-ID` from inbound headers (falling back to a fresh 8-hex-char
  token), pushes the value into OpenTelemetry baggage as
  `mangomas.correlation_id`, and echoes it on the outgoing response.
- **SecretsProvider seam** (`src/mangomas/secrets/`): `SecretsProvider`
  protocol, `EnvSecretsProvider` env-var backend, and module-level
  `secrets_registry`. `LLMSettings.secret_ref` (new optional field) is
  resolved at orchestrator-build time and used to replace `api_key` when
  set. Cloud backends are deferred to Phase 3.
- **`Registry.scoped()`** context manager for test-scoped provider
  substitution. Restores the prior binding (or removes the entry if absent)
  on block exit, even when the wrapped block raises.
- **Shared LM Studio E2E fixtures** in `tests/lmstudio/conftest.py`:
  `lmstudio_base_url`, `lmstudio_model`, `lmstudio_orchestrator`,
  `lmstudio_app` (ASGITransport over the real `create_app`).

### Changed

- `_llm_registry` renamed to `llm_registry` (public) so tests can swap LLM
  factories via `Registry.scoped()` without poking module internals.
- `AccessLogMiddleware` now emits both `request_id` and `correlation_id`
  fields on every access-log line (today they always carry the same value).
- `scripts/check_coverage.py` adds 100 % floors for `src/mangomas/secrets/*.py`
  and `src/mangomas/correlation.py`.
- **Correlation primitives moved to `src/mangomas/correlation.py`** (top-level)
  to break the `mangomas.api → mangomas.telemetry` import cycle without a
  lazy import. `mangomas.api.correlation` remains as a backwards-compatible
  re-export shim — existing imports continue to work. (Shim removed in v0.3.0.)
- **Streaming buffered-fallback extracted** into
  `mangomas.agents._streaming.stream_with_buffered_fallback`. The three
  agents (`ChatAgent`, `PlannerAgent`, `ReviewerAgent`) now delegate to a
  single helper after building their respective message lists, replacing
  three near-identical 18-line `_do_stream` bodies. The fallback warning
  text is a module-level constant so log-grep filters survive future edits.
- `ruff` pinned to `>=0.11,<1.0` in dev deps; `respx`/`tests.*` mypy
  overrides added so the CI scope (`src tests scripts`) passes `--strict`.

### Fixed

- **CI lint job (PLC0415)**: Local ruff 0.8.0 and CI's newer ruff disagreed on
  whether `PLC0415` (lazy import) was enabled, causing CI to fail with errors
  local couldn't reproduce. Root cause addressed structurally: the lazy import
  in `telemetry.py` was removed (the cycle is gone now that correlation lives
  at top level) and `cli/main.py`'s lazy `build_orchestrator` import was
  promoted to module-level.
- **Inbound `X-Request-ID` sanitisation**: Inbound values are now passed
  through `mangomas.correlation.sanitize_inbound_correlation_id`, which strips
  characters outside `[A-Za-z0-9_\-./:]` (blocking CR/LF log-injection) and
  truncates at `MAX_CORRELATION_ID_LENGTH = 64` characters. Falls back to a
  fresh generated id when the inbound value is empty, whitespace-only, or
  entirely composed of disallowed characters.
- **`X-Request-ID` echoed on error responses**: The middleware now sets the
  header in its `finally` block (so handled `MangomasError` JSONResponses and
  any 4xx/5xx produced by FastAPI exception handlers carry it) and
  synthesises its own `PlainTextResponse` with the header attached when an
  unhandled exception escapes `call_next`, instead of re-raising and losing
  the correlation handle inside Starlette's default `ServerErrorMiddleware`.
- **`Registry` thread-safety**: All mutations and reads now acquire an
  internal `threading.RLock`, matching the thread-safety guarantee documented
  in `docs/architecture/c3-component.md`. `RLock` (not `Lock`) so `scoped()`
  can call `get`/`register` under the same lock without deadlocking.

## [0.1.0] — 2026-05-13

### Fixed

- `LMStudioClient`: wrap raw `httpx` exceptions into typed `LLMTimeout` /
  `LLMUnavailable` / `LLMBadResponse` subclasses across `complete()`, `ping()`,
  and `_stream_impl()` so API responses always carry the structured error
  envelope and correct HTTP status mapping.
- `SQLiteRepository`: serialise all access to the shared connection with a
  `threading.Lock` to make concurrent writes from `dispatch_fan_out` safe.
- `ToolCallParser`: bare `{...}` objects that fail JSON validation now return
  `None` (treated as prose) instead of raising; only fenced ```json``` blocks
  raise `LLMBadResponse` on malformed JSON.
- `FileMemoryRepository`: use UTC for episodic file naming so filenames are
  stable across timezones and cloud regions.
- `SummarizeAgent`: include every message in each historical turn (not only
  the first user message) so multi-message turns retain full context.
- `AccessLogMiddleware`: wrap `call_next` in `try/except/finally` so failed
  requests still emit an INFO access log with status `500` and latency.
- `telemetry.configure_telemetry`: attach `TraceContextFilter` to the
  configured handler (not the root logger) so `trace_id` / `span_id` are
  injected into every log record from child loggers.
- `api/app.py` lifespan: simplify shutdown checks; `ctx.repo.close()` is
  protected by a `None` check rather than `hasattr`.
- `Dockerfile`: create `/app/data` and `/data` and `chown` to the `mangomas`
  user so the default SQLite path and compose volume are writable from the
  non-root runtime user.

### Added

**Core platform**
- `Agent` protocol with `handle(request, ctx)` contract; `StreamingAgent` extension protocol for token-level streaming.
- `Orchestrator` with `dispatch` (buffered) and `stream_dispatch` (async-generator streaming) methods; lazy OpenTelemetry tracer (no module-level tracer capture).
- `Registry[T]` — generic, reusable lookup store; drives both provider and agent wiring.
- `AgentContext` — immutable context injected into every agent invocation (LLM client, turn repository, optional memory repository).

**Agents**
- `ChatAgent` — single-turn conversational agent with streaming fallback and warning log when the active LLM does not implement `StreamingLLMClient`.
- `SummarizeAgent` — fetches recent conversation history and requests an LLM summary.
- `ToolAgent` — multi-step control loop with tool-call parsing and execution.
- `PlannerAgent` / `ReviewerAgent` — plan-then-review composition pattern.

**Adapters**
- `LMStudioClient` — OpenAI-compatible HTTP adapter targeting `/v1/chat/completions` and `/v1/models`; supports buffered completion, streaming, ping, and graceful close via `aclose()`.
- `SQLiteRepository` — lightweight `TurnRepository` implementation backed by SQLite.
- File-based memory provider.

**API**
- FastAPI application factory `create_app(orchestrator=None)` — lifespan manages startup/shutdown.
- Routes: `GET /healthz`, `GET /health` (alias), `GET /readyz`, `GET /ready` (alias), `GET /agents`, `POST /agents/{name}/invoke`, `POST /agents/{name}/stream`.
- JSON SSE envelope: `{"event": "token", "data": {"content": "..."}, "content": "..."}` with `{"event": "done"}` sentinel; top-level `content` field kept for backwards compatibility.
- `AccessLogMiddleware` — per-request structured access log.
- `TraceMiddleware` — OpenTelemetry span per request.
- Structured error envelope `{"error": "...", "message": "...", "detail": "..."}` mapped from domain error classes via MRO-based `_error_status`.

**CLI**
- `mangomas chat` — interactive single-turn CLI backed by the full agent stack.

**Composition**
- `agent_registry: Registry[AgentFactory]` — seeded with `chat` and `summarize`; extensible without core changes.
- `_llm_registry` and `_storage_registry` — provider registries for LLM and storage adapters.
- `build_orchestrator(settings)` — assembles the full runtime from settings alone; no hardcoded class names outside `composition.py`.

**Observability**
- Structured logging throughout; `extra={}` fields on all error and warning paths.
- OpenTelemetry tracing via `opentelemetry-sdk`; console exporter for local development.
- `/readyz` aggregates LLM ping and DB connectivity into a `ReadinessReport`.

**Infrastructure**
- Multi-stage Dockerfile; runtime stage runs as non-root user `mangomas`, honours `$PORT`, and includes a `HEALTHCHECK` against `/healthz`.
- `.github/workflows/ci.yml` — ruff check, ruff format --check, mypy --strict over `src tests scripts`, pytest with coverage, per-package coverage floors, codecov upload.
- `pyproject.toml` — hatchling build, all dev tooling configured, `asyncio_mode = "auto"`, `lmstudio` and `integration` pytest markers registered.
- `pyrightconfig.json` and minimal `typings/hypothesis` stubs for VS Code editor parity with CLI mypy.

**Documentation**
- `docs/adr/0001-cloud-targets.md` — cloud target swap matrix (ADR-001).

### Changed

- mypy strict gate widened from `src` only to `src tests scripts` (60 source files).
- Provider wiring moved from ad-hoc class instantiation to registry-based composition; new providers require no changes to core or API layers.
- `Orchestrator` tracer changed from module-level capture to lazy `trace.get_tracer(__name__)` call inside method bodies.

### Security / Operations

- Docker runtime uses a non-root user; no secrets or credentials in the image.
- All configuration is env-driven via `MANGOMAS_*` prefix; no hardcoded endpoints, model ids, or credentials in source.
- `.gitignore` excludes `.venv/`, `data/`, `memory/`, `.env`, coverage artefacts, caches, and generated files.
- Request-scoped tracing without leaking spans across async contexts.

[0.3.1]: https://github.com/Mango-Metrics-NLM/MangoMas_V2/releases/tag/v0.3.1
[0.3.0]: https://github.com/Mango-Metrics-NLM/MangoMas_V2/releases/tag/v0.3.0
[0.2.0]: https://github.com/Mango-Metrics-NLM/MangoMas_V2/releases/tag/v0.2.0
[0.1.0]: https://github.com/Mango-Metrics-NLM/MangoMas_V2/releases/tag/v0.1.0
