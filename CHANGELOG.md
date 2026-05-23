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

### Added

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
[0.1.0]: https://github.com/Mango-Metrics-NLM/MangoMas_V2/releases/tag/v0.1.0
