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

<!-- next release goes above this line -->
[0.1.0]: https://github.com/Mango-Metrics-NLM/MangoMas_V2/releases/tag/v0.1.0
