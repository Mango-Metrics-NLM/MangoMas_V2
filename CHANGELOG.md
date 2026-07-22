# Changelog

All notable changes to Mango-Mas V2 are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/).

---

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

---

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

## [Unreleased]

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

<!-- next release goes above this line -->
[0.3.1]: https://github.com/Mango-Metrics-NLM/MangoMas_V2/releases/tag/v0.3.1
[0.3.0]: https://github.com/Mango-Metrics-NLM/MangoMas_V2/releases/tag/v0.3.0
[0.2.0]: https://github.com/Mango-Metrics-NLM/MangoMas_V2/releases/tag/v0.2.0
[0.1.0]: https://github.com/Mango-Metrics-NLM/MangoMas_V2/releases/tag/v0.1.0
