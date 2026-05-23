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

## [Unreleased]

## [0.3.0] — 2026-05-23

Closes the GCP swap-matrix entries from ADR-001: Vertex AI LLM,
Cloud SQL Postgres storage, and GCP Secret Manager all ship together
behind the existing registry + protocol seams. No core changes; every
provider is selectable via `Settings`. Identity throughout is
Application Default Credentials / Workload Identity Federation only —
service-account JSON keys are not accepted by code or configuration.

### Added

- **Vertex AI LLM provider** (`src/mangomas/adapters/llm/vertex.py`):
  `VertexLLMClient` satisfies `LLMClient`, `StreamingLLMClient`, and
  `PingableLLMClient`. Backed by `google-cloud-aiplatform` + Gemini.
  The sync SDK is wrapped in `asyncio.to_thread`; all SDK imports
  live inside function bodies so the module stays importable without
  the optional `vertex` extra. Activate via
  `MANGOMAS_LLM__PROVIDER=vertex` + `MANGOMAS_LLM__PROJECT=...`.
  6-scenario E2E suite under `tests/vertex/` mirrors the LM Studio
  scenario plan; gated on `RUN_VERTEX=1`. See
  `docs/testing/vertex-e2e.md`.
- **Cloud SQL / Postgres storage provider**
  (`src/mangomas/adapters/storage/postgres.py`): `PostgresRepository`
  satisfies `TurnRepository` and the new `AsyncCloseableRepository`
  extension. Backed by `asyncpg` with a connection pool — no
  `threading.Lock` (native async). Lazy pool creation preserves the
  existing `_sqlite_factory(cfg: DBSettings) -> SQLiteRepository`
  factory shape. A JSONB codec is registered on every connection so
  `list_turns` returns dicts (matching SQLite's row shape). Activate
  via `MANGOMAS_DB__PROVIDER=postgres` +
  `MANGOMAS_DB__URL=postgresql://...`. testcontainers-driven
  integration suite under `tests/postgres/` gated on `RUN_POSTGRES=1`.
  See `docs/testing/postgres-integration.md`.
- **GCP Secret Manager provider** (`src/mangomas/secrets/gcp.py`):
  `GCPSecretManagerProvider` satisfies `SecretsProvider`. Supports
  both short ids (resolved against
  `MANGOMAS_SECRETS__PROJECT_ID` + `MANGOMAS_SECRETS__DEFAULT_VERSION`)
  and full `projects/.../secrets/.../versions/...` resource paths. All
  failure modes collapse to `None` per ADR-002 so
  `_resolve_llm_secrets` continues to fall back to the inline `api_key`
  in local dev. Activate via `MANGOMAS_SECRETS__PROVIDER=gcp`.
- **`AsyncCloseableRepository` extension protocol**
  (`src/mangomas/adapters/storage/base.py`): adapters with async-pool
  teardown expose `aclose()`; the FastAPI lifespan and CLI close path
  dispatch on its presence. SQLite continues to satisfy the bare
  `TurnRepository` protocol unchanged.
- **CLI close path**: `agents`, `chat`, and `history` commands now
  wrap their work in `try/finally: asyncio.run(_close_orchestrator(orch))`
  so asyncpg pools don't leak at CLI process exit.
- **`tests/test_sqlite_concurrency.py`**: 50-way `asyncio.gather`
  fan-out of `save_turn` against in-memory SQLite. Locks the v0.1.0
  `threading.Lock` fix that previously had no direct regression test.
  Runs in the default suite.
- **`tests/postgres/test_concurrency.py`**: symmetric 50-way fan-out
  test against `PostgresRepository` — pins the asyncpg pool's
  concurrent-write contract. Gated on `RUN_POSTGRES=1`.
- **ADR-002** (`docs/adr/0002-secrets-provider-error-semantics.md`):
  records the choice to collapse cloud secrets backend failures into
  `None` rather than raise, with a v0.4.0 follow-up for a
  `SecretsSettings.strict` opt-in.
- **`docs/architecture/cloud-providers.md`**: single combined page
  covering Vertex / Postgres / GCP Secret Manager — env-var contracts,
  registration cites, ambient-identity guidance, the rule-of-three
  rationale for not extracting a shared lazy-SDK base class yet.
- **New optional extras** in `pyproject.toml`: `vertex`, `postgres`,
  `gcp`, and meta-extra `cloud`. `dev` adds `asyncpg` and
  `testcontainers` so postgres unit tests and the testcontainer suite
  collect cleanly; vertex and gcp SDKs stay out of `dev` because unit
  tests mock at the constructor boundary.
- **New pytest markers**: `postgres`, `vertex`, `gcp_secrets` and
  corresponding `RUN_*` env-var gates in
  `tests/conftest.py::pytest_collection_modifyitems`.
- **Postgres compose profile** in `docker-compose.yml`: opt-in via
  `docker compose --profile postgres up -d postgres`; credentials
  local-only.

### Changed

- `LLMSettings` gains optional `project`, `location`,
  `max_output_tokens` for Vertex (defaults preserve LM Studio behaviour).
- `DBSettings` gains optional `pool_min`, `pool_max`,
  `connect_timeout_seconds`, `statement_timeout_seconds` for Postgres
  (defaults preserve SQLite behaviour).
- `SecretsSettings` gains optional `project_id`, `timeout_seconds`,
  `default_version` for GCP (defaults preserve env-backend behaviour).
- FastAPI lifespan and CLI close path dispatch on
  `hasattr(repo, "aclose")` — backwards-compatible with
  `SQLiteRepository`'s sync `close()`.
- `build_orchestrator` log line gains a `secrets_provider` field.
- `pyproject.toml`: version bumped to `0.3.0`; mypy
  `ignore_missing_imports` extended to cover `google.*`, `vertexai.*`,
  `asyncpg.*`, `testcontainers.*`. `tests/*` per-file ruff ignore
  extends to `SLF001` so tests can introspect adapter internals.

### Removed

- `src/mangomas/api/correlation.py` re-export shim (the
  backwards-compat shim from v0.2.0; the single internal consumer
  migrated to `mangomas.correlation` direct imports).
- `tests/conftest.py` fakes re-export (the one-cycle migration window
  from v0.2.0; `tests/test_agent.py` migrated to direct
  `tests.fakes` imports).

### Security / Operations

- All cloud adapters consume **ambient identity only** (ADC /
  Workload Identity Federation). Service-account JSON keys are not
  accepted by configuration, env, or code.
- Secret values are **never** logged. Cloud secret resource paths are
  truncated to the short id in `extra={}` log fields. Postgres DSNs
  are **never** logged in full; only the parsed host appears.
- ADR-002 documents that rotated secrets can silently degrade to an
  inline `api_key`; operators MUST alert on
  `logger=mangomas.secrets.gcp severity=ERROR`.

## [0.2.0] — 2026-05-13

### Added

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
  re-export shim — existing imports continue to work.
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
[0.3.0]: https://github.com/Mango-Metrics-NLM/MangoMas_V2/releases/tag/v0.3.0
[0.2.0]: https://github.com/Mango-Metrics-NLM/MangoMas_V2/releases/tag/v0.2.0
[0.1.0]: https://github.com/Mango-Metrics-NLM/MangoMas_V2/releases/tag/v0.1.0
