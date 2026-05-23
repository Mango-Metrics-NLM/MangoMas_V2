# Mango-Mas V2 — GCP Swap-in & Evaluation Implementation Plan

- **Branch:** `claude/information-roadmap-planning-iGcin`
- **Date:** 2026-05-23
- **Target releases:** v0.3.0 (M1–M2), v0.4.0 (M3–M6), v0.5.0 (M7)
- **Status:** Draft for review

---

## Executive Summary

- The v0.2.0 platform already ships every architectural seam this plan needs: `Registry[T]` with `scoped()`, `SecretsProvider` protocol + `secrets_registry`, `LLMClient` / `StreamingLLMClient` / `PingableLLMClient` protocols, `TurnRepository` protocol, a single `configure_telemetry()` entry point, `JsonFormatter` for Cloud Logging, `_streaming.stream_with_buffered_fallback` for streaming parity, and `composition.build_orchestrator` as the only wiring point. No core protocol changes are required for M1–M6.
- **M1 (cut v0.2.0)** is purely mechanical — promote the `[Unreleased]` block, bump version in `pyproject.toml` and `api/app.py`, tag, link the footer in `CHANGELOG.md`. It unblocks every subsequent milestone because each cuts off `main` at a known SemVer baseline.
- **M2 (Cloud Trace OTLP exporter)** lands the `MANGOMAS_TELEMETRY__EXPORTER=gcp` flag and a new `TelemetrySettings` subclass. This is the smallest cloud-adapter milestone and is a prerequisite for sane debugging of M3–M5 once they hit a real GCP project, so it ships next.
- **M3 (VertexLLMClient)** and **M4 (GCP Secret Manager backend)** can land in parallel because Vertex needs Secret Manager access only at runtime — the SDK reads ambient credentials, not `LLMSettings.api_key`. They share zero file edits except `composition.py`, `config.py`, and `pyproject.toml` extras, so a clean rebase between them is straightforward. Merge M4 first because it has a smaller surface (one new file, one factory registration).
- **M5 (PostgresRepository)** depends on M2's structured-log envelope landing so DB latency and errors surface uniformly. The driver choice (`asyncpg`) is the one open question; the file plan assumes asyncpg.
- **M6 (Cloud Run deployment pipeline)** depends on M2/M3/M4/M5 because the GitHub workflow needs the full env-var contract documented in one place. It introduces a new `deploy/` directory and a separate workflow (`deploy.yml`), keeping `ci.yml` untouched.
- **M7 (evaluation harness)** is the only milestone that introduces a *new* protocol (`Scorer`). It runs in parallel to M3–M6 because it does not touch any cloud adapter — the harness pipes through the same `Orchestrator` interface the API uses today. Justification for the new protocol is documented under M7.
- **Coverage and CI guardrails** are preserved end-to-end: every new package gets a per-package floor in `scripts/check_coverage.py`, every cloud SDK lives behind an optional extra, and parity-test gates (`RUN_VERTEX=1`, `RUN_POSTGRES=1`, `RUN_EVALS=1`) mirror the existing `RUN_LMSTUDIO=1` gating pattern verbatim.

---

## Milestone M1 — Cut v0.2.0 Release

### Objective.
Promote the `[Unreleased]` section of `CHANGELOG.md` to a dated `[0.2.0]` block, bump version metadata, tag, and link the comparison footer so downstream milestones cut off a stable SemVer baseline.

### Dependency edge.
None — this is the entry point. Consumes nothing; produces a tagged `v0.2.0` commit that every later milestone branches from.

### Gap analysis.
- **Exists.**
  - `/home/user/MangoMas_V2/CHANGELOG.md` already holds the full `[Unreleased]` block (lines 102–179), ready to be renamed.
  - `/home/user/MangoMas_V2/pyproject.toml` line 7 carries `version = "0.1.0"`.
  - `/home/user/MangoMas_V2/src/mangomas/api/app.py` line 101 carries `version="0.1.0"` in `FastAPI(title=...)`.
  - Existing footer link pattern at `CHANGELOG.md:181` (`[0.1.0]: https://github.com/...`).
- **Missing.**
  - A dated `## [0.2.0] — 2026-05-23` heading replacing `## [Unreleased]`.
  - A fresh empty `## [Unreleased]` block above the v0.2.0 block (so M2+ have somewhere to write).
  - Git tag `v0.2.0`.
  - Footer link `[0.2.0]: .../releases/tag/v0.2.0` and updated `[Unreleased]: .../compare/v0.2.0...HEAD`.
- **Redundant-if-added.** No new code. No "release script" — Keep a Changelog discipline plus a single tag is sufficient at this stage.

### File-level plan.
| File | Change | Purpose |
|---|---|---|
| `/home/user/MangoMas_V2/CHANGELOG.md` | modify | Rename `[Unreleased]` → `[0.2.0] — 2026-05-23`; insert empty `[Unreleased]` block above; add footer links. |
| `/home/user/MangoMas_V2/pyproject.toml` | modify | Bump `version = "0.2.0"`. |
| `/home/user/MangoMas_V2/src/mangomas/api/app.py` | modify | Bump `FastAPI(..., version="0.2.0")` on line 101. |
| `/home/user/MangoMas_V2/README.md` | modify | Update install-instructions version reference if any (verify before edit). |

### Settings additions.
None.

### Registry wiring.
None.

### Logging touchpoints.
None.

### Error model.
None.

### Test strategy.
- **Unit.** Existing test suite must remain green on the bumped version; no new tests.
- **Integration.** None.
- **Parity / E2E.** None.

Manual checks: `git tag --verify v0.2.0`, `pytest -q`, `python -m pytest tests/lmstudio --no-cov -q` with `RUN_LMSTUDIO=1` on a developer box.

### Backwards-compatibility checklist.
- No code changes affecting public API.
- The empty `[Unreleased]` block must exist so M2+ have a place to write entries without rewriting history.
- v0.2.0 footer link points at `releases/tag/v0.2.0` (consistent with v0.1.0 line 181).

### Acceptance criteria.
- `git tag -l v0.2.0` returns the tag.
- `CHANGELOG.md` contains a dated `## [0.2.0] — 2026-05-23` block and an empty `## [Unreleased]` block above it.
- `pyproject.toml::project.version == "0.2.0"`.
- `FastAPI.openapi()["info"]["version"] == "0.2.0"` (assertable in a smoke test).
- CI green on the tagged commit.

### Risk + mitigations.
- **Risk:** Forgotten version reference (e.g. in docs or `README.md`). **Mitigation:** `grep -rn '0\.1\.0' src docs README.md pyproject.toml` before tagging.
- **Risk:** Empty `[Unreleased]` block deleted by a future commit because the section looks redundant. **Mitigation:** include the `<!-- next release goes above this line -->` marker already present at line 180.

---

## Milestone M2 — Cloud Trace OTLP Exporter

### Objective.
Add an optional Cloud Trace OTLP exporter behind `configure_telemetry()`, selected by `MANGOMAS_TELEMETRY__EXPORTER=gcp`, so spans emitted by `TraceMiddleware` and `Orchestrator` land in Cloud Trace when the flag is set; console exporter remains the default.

### Dependency edge.
Requires M1 (clean v0.2.0 baseline). Consumes:
- `configure_telemetry()` entry point at `/home/user/MangoMas_V2/src/mangomas/telemetry.py:108`.
- `JsonFormatter` at the same file (already Cloud Logging-compatible).
- W3C `TraceContextTextMapPropagator` already installed via `set_global_textmap` (`telemetry.py:141`).
- `Settings.log.format` already supports `"json"` via `LogSettings.format` (config.py:91).

### Gap analysis.
- **Exists.**
  - `configure_telemetry(service_name, log_level, log_format)` — single bootstrap call.
  - `JsonFormatter` — emits Cloud Logging-compatible JSON envelopes.
  - `TraceContextFilter` + `CorrelationFilter` — handler-attached log filters injecting trace/span/correlation ids.
  - `TracerProvider(resource=Resource.create({"service.name": ...}))` — only the span processor needs swapping; resource and propagator stay.
  - `MANGOMAS_LOG__FORMAT=json` already wires Cloud Logging-compatible logs (per `observability.md:97`).
- **Missing.**
  - A `TelemetrySettings` subclass on `Settings` with an `exporter: Literal["console", "gcp"] = "console"` field.
  - `MANGOMAS_TELEMETRY__EXPORTER` env var routing.
  - An optional dependency `opentelemetry-exporter-gcp-trace` in `pyproject.toml` `[project.optional-dependencies].gcp`.
  - A factory in `telemetry.py` that selects `ConsoleSpanExporter` vs `CloudTraceSpanExporter` based on `TelemetrySettings.exporter`.
  - A `TelemetryConfigError` (extends `ConfigError`) for the case where `exporter="gcp"` is set but the optional extra is not installed.
- **Redundant-if-added.**
  - Do **not** add a new `BatchSpanProcessor` toggle — Cloud Trace requires batch, console keeps simple; the exporter selection is sufficient.
  - Do **not** add a new `configure_logging` function — `configure_telemetry` already owns the handler and its filters.
  - Do **not** introduce a new `Tracer` wrapper — `mangomas.telemetry.get_tracer` already exists and remains untouched.

### File-level plan.
| File | New/Modify | Purpose |
|---|---|---|
| `/home/user/MangoMas_V2/src/mangomas/telemetry.py` | modify | Add `_build_span_exporter(settings)` factory with lazy import of `opentelemetry.exporter.cloud_trace.CloudTraceSpanExporter`; switch `configure_telemetry` to consume the new `TelemetrySettings`. |
| `/home/user/MangoMas_V2/src/mangomas/config.py` | modify | Add `class TelemetrySettings(BaseModel)` with `exporter: Literal["console", "gcp"]`, `gcp_project_id: str | None`, `service_name: str`; expose via `Settings.telemetry`. |
| `/home/user/MangoMas_V2/src/mangomas/errors.py` | modify | Add `TelemetryConfigError(ConfigError)` with `code = "telemetry_config_error"`. |
| `/home/user/MangoMas_V2/src/mangomas/api/app.py` | modify | Pass `settings.telemetry` into `configure_telemetry(...)` lifespan call; map `TelemetryConfigError` in `_ERROR_STATUS` (inherits `ConfigError` → already 400, no extra entry needed). |
| `/home/user/MangoMas_V2/pyproject.toml` | modify | Add `[project.optional-dependencies].gcp` with `opentelemetry-exporter-gcp-trace>=1.7` and `google-cloud-logging>=3.10`. |
| `/home/user/MangoMas_V2/tests/test_telemetry.py` | modify | Add tests: `console` default, `gcp` exporter selected when extra installed, `TelemetryConfigError` when extra missing (mock import failure). |
| `/home/user/MangoMas_V2/tests/test_config.py` | modify | Assert `Settings.telemetry.exporter == "console"` by default, env-var override routes correctly. |
| `/home/user/MangoMas_V2/scripts/check_coverage.py` | modify | No new floor — `telemetry.py` covered under `agents/*.py`-adjacent default; existing global floor catches it. (Re-evaluate if telemetry grows; flagged in cross-cutting section.) |
| `/home/user/MangoMas_V2/docs/architecture/observability.md` | modify | Update "What ships in v0.2.0 vs Phase 3" section: move Cloud Trace exporter line into "shipped in v0.3.0". |
| `/home/user/MangoMas_V2/docs/adr/0001-cloud-targets.md` | modify | Mark Telemetry row "in progress → shipped v0.3.0" footnote. |

### Settings additions.
| Env var | Settings field | Default | Validation |
|---|---|---|---|
| `MANGOMAS_TELEMETRY__EXPORTER` | `TelemetrySettings.exporter` | `"console"` | `Literal["console", "gcp"]` |
| `MANGOMAS_TELEMETRY__GCP_PROJECT_ID` | `TelemetrySettings.gcp_project_id` | `None` | `str \| None`. If `exporter == "gcp"` and value is `None`, raise `TelemetryConfigError` at `configure_telemetry` time. The Cloud Trace SDK can auto-detect via ambient credentials; explicit override remains supported. |
| `MANGOMAS_TELEMETRY__SERVICE_NAME` | `TelemetrySettings.service_name` | `"mangomas"` | `str`. Surfaces in span `resource.service.name`. |

`Settings` gains `telemetry: TelemetrySettings = Field(default_factory=TelemetrySettings)`.

### Registry wiring.
No changes to `composition.py`. Telemetry is bootstrapped in `api/app.py::_lifespan` (line 75) which is the single startup hook.

### Logging touchpoints.
- `telemetry.py::_build_span_exporter` — at `INFO`: `"Telemetry exporter configured"` with `extra={"exporter": settings.exporter, "service_name": settings.service_name}`.
- At `ERROR` when the Cloud Trace SDK import fails: `"Cloud Trace exporter requested but opentelemetry-exporter-gcp-trace not installed"` with `extra={"required_extra": "mangomas[gcp]"}`. Raises `TelemetryConfigError`.

### Error model.
- `TelemetryConfigError(ConfigError)` — `code = "telemetry_config_error"`. Inherits `ConfigError → 400` mapping in `_ERROR_STATUS`; no new mapping line required. Raised at startup, so it surfaces via the uvicorn process crashing rather than an HTTP response — the structured error envelope is irrelevant here, but the `code` is preserved for the lifespan crash log.

### Test strategy.
- **Unit.**
  - `tests/test_telemetry.py` — exporter selection branches. Use `monkeypatch.setitem(sys.modules, ...)` to simulate the optional import being present/absent.
  - `tests/test_config.py` — `Settings(_env_file=None, telemetry=TelemetrySettings(exporter="gcp", gcp_project_id="p"))` constructs cleanly; missing `gcp_project_id` defers validation to `configure_telemetry`.
  - No new Fakes needed; existing tests already monkeypatch `configure_telemetry` (`tests/test_api.py:134`).
- **Integration.** Optional `RUN_GCP_TRACE=1` smoke that asserts the Cloud Trace SDK initializes without raising when the credentials are present (skipped by default; doc-only — no GCP resources created by this branch per ADR-001 / NEXT_STEPS.md "Deferred").
- **Parity / E2E.** Not applicable.

### Backwards-compatibility checklist.
- `configure_telemetry()` with no args still works (uses defaults → console exporter).
- `MANGOMAS_LOG__FORMAT=json` unchanged.
- `Settings.log` unchanged (telemetry is a sibling, not a child, of log).
- Existing `tests/test_telemetry.py::test_configure_telemetry_idempotent` continues to pass without modification.
- Deprecation notice: none.

### Acceptance criteria.
- `MANGOMAS_TELEMETRY__EXPORTER` unset → console exporter, identical to v0.2.0 behaviour.
- `MANGOMAS_TELEMETRY__EXPORTER=gcp` with extra installed → `CloudTraceSpanExporter` registered, `BatchSpanProcessor` used, INFO log emitted.
- `MANGOMAS_TELEMETRY__EXPORTER=gcp` with extra NOT installed → `TelemetryConfigError` raised at startup with structured `extra={"required_extra": "mangomas[gcp]"}`.
- `pip install -e ".[gcp]"` succeeds and pulls `opentelemetry-exporter-gcp-trace`.
- `configure_telemetry` remains idempotent.

### Risk + mitigations.
- **Risk:** Cloud Trace SDK pulls a heavy dep tree that breaks `pip install -e ".[dev]"`. **Mitigation:** Keep it in `[gcp]` extra only; `dev` does not transitively depend on `gcp`.
- **Risk:** Tests that mock `configure_telemetry` (`tests/test_api.py:134`) break because of changed signature. **Mitigation:** keep the existing kwarg signature (`service_name`, `log_level`, `log_format`) but add a new `telemetry: TelemetrySettings | None = None` kwarg with a `None` default that falls through to console behaviour.

---

## Milestone M3 — Vertex AI LLM Provider

### Objective.
Ship `VertexLLMClient` satisfying `LLMClient` + `StreamingLLMClient` + `PingableLLMClient`, registered as `llm_registry.register("vertex", ...)`, activatable via `MANGOMAS_LLM__PROVIDER=vertex`, with a parity test suite mirroring `tests/lmstudio/` gated on `RUN_VERTEX=1`.

### Dependency edge.
Requires M1 baseline and benefits from M2 (Cloud Trace) for tracing real Vertex calls but does not strictly depend on it. Consumes:
- `LLMClient` / `StreamingLLMClient` / `PingableLLMClient` protocols at `/home/user/MangoMas_V2/src/mangomas/adapters/llm/base.py`.
- `llm_registry` at `/home/user/MangoMas_V2/src/mangomas/composition.py:45`.
- `_resolve_llm_secrets` seam at `composition.py:54-74` (Vertex auth via Workload Identity uses ambient creds; `LLMSettings.api_key` and `secret_ref` are bypassed for Vertex but the seam remains unmodified).
- Existing LM Studio E2E fixtures pattern in `tests/lmstudio/conftest.py` — Vertex parity suite copies the structure: shared fixtures + per-scenario test file.

### Gap analysis.
- **Exists.**
  - All three protocols Vertex must satisfy.
  - `Message` model with `role: Literal["system", "user", "assistant", "tool"]` — maps cleanly to Vertex `Content.role` / `Part.text`.
  - `_translate_httpx_error`-style pattern in `lmstudio.py:22` — Vertex will mirror this with a `_translate_vertex_error` helper.
  - `LLMTimeout` / `LLMUnavailable` / `LLMBadResponse` error classes; no new error subclasses needed.
  - `FakeLLM` in `tests/fakes.py` already supports `complete`, `stream`, `ping`, `aclose` — Vertex unit tests reuse it via the `LLMClient` protocol; no new Fake needed.
  - `Registry.scoped()` for parity tests that need to swap factories.
- **Missing.**
  - `src/mangomas/adapters/llm/vertex.py` — the new adapter.
  - Vertex-specific settings fields: `LLMSettings.gcp_project_id`, `LLMSettings.gcp_location`, `LLMSettings.gcp_credentials_path`.
  - `llm_registry.register("vertex", _vertex_factory)` in `composition.py`.
  - Optional dep `google-cloud-aiplatform` in `[project.optional-dependencies].gcp`.
  - Parity test directory `tests/vertex/` with `conftest.py` + 5 scenario files mirroring `tests/lmstudio/`.
  - `RUN_VERTEX=1` gate in `tests/conftest.py::pytest_collection_modifyitems`.
  - `vertex` pytest marker in `pyproject.toml::[tool.pytest.ini_options].markers`.
  - Per-package coverage floor for `src/mangomas/adapters/llm/vertex.py` in `scripts/check_coverage.py` (suggested 85 %, matching adapter floor).
- **Redundant-if-added.**
  - Do **not** add a new "VertexStreamingClient" subclass — one client implements both protocols, matching `LMStudioClient`'s shape.
  - Do **not** route Vertex through `_resolve_llm_secrets` — Vertex uses ambient GCP credentials (Workload Identity per ADR-001 row 6). `secret_ref` stays optional and is ignored by the Vertex factory.
  - Do **not** write a new "auth helper" — `google.auth.default()` does it.
  - Do **not** rebuild the buffered-fallback warning logic — `_streaming.stream_with_buffered_fallback` already handles non-streaming LLM clients uniformly. If Vertex always streams, this helper is never triggered; if a Vertex variant doesn't, the existing helper covers it.

### File-level plan.
| File | New/Modify | Purpose |
|---|---|---|
| `/home/user/MangoMas_V2/src/mangomas/adapters/llm/vertex.py` | new | `VertexLLMClient` with `complete()`, `stream()` (async gen returning `AsyncIterator[str]`), `ping()` (calls `aiplatform.Endpoint.list` or `GenerativeModel(...).count_tokens("ping")`), `aclose()` (no-op or `await client.transport.close()`). Lazy import of `google.cloud.aiplatform` inside `__init__` / factory; `TYPE_CHECKING` guard for type-only imports. |
| `/home/user/MangoMas_V2/src/mangomas/adapters/llm/__init__.py` | modify | Conditionally re-export `VertexLLMClient` guarded by an `ImportError` swallow — keeps base install working. |
| `/home/user/MangoMas_V2/src/mangomas/config.py` | modify | Extend `LLMSettings` with `gcp_project_id: str \| None = None`, `gcp_location: str \| None = None`, `gcp_credentials_path: str \| None = None`, `request_timeout_seconds: float \| None = None` (re-use `timeout_seconds` where possible). |
| `/home/user/MangoMas_V2/src/mangomas/composition.py` | modify | Add `_vertex_factory(cfg: LLMSettings) -> VertexLLMClient` with lazy import; `llm_registry.register("vertex", _vertex_factory)`. Guard registration behind a try/except ImportError so missing extra does not break import (factory raises `ConfigError` if invoked without the extra). |
| `/home/user/MangoMas_V2/src/mangomas/errors.py` | modify | No new classes. Vertex maps SDK errors onto existing `LLMTimeout` / `LLMUnavailable` / `LLMBadResponse` via a `_translate_vertex_error` helper local to `vertex.py`. |
| `/home/user/MangoMas_V2/pyproject.toml` | modify | Add `google-cloud-aiplatform>=1.60` to `[project.optional-dependencies].gcp`; add `vertex` to `[tool.pytest.ini_options].markers`. |
| `/home/user/MangoMas_V2/tests/vertex/__init__.py` | new | Package marker. |
| `/home/user/MangoMas_V2/tests/vertex/conftest.py` | new | `vertex_project_id`, `vertex_location`, `vertex_model`, `vertex_orchestrator`, `vertex_app` fixtures — mirrors `tests/lmstudio/conftest.py:131-169` structure, gated on `RUN_VERTEX=1`. |
| `/home/user/MangoMas_V2/tests/vertex/test_chat_invoke.py` | new | Parity of `tests/lmstudio/test_chat_invoke.py`. |
| `/home/user/MangoMas_V2/tests/vertex/test_chat_stream.py` | new | Parity of `tests/lmstudio/test_chat_stream.py`. |
| `/home/user/MangoMas_V2/tests/vertex/test_stream_fallback.py` | new | Parity of `tests/lmstudio/test_stream_fallback.py` using `Registry.scoped()` with a `NonStreamingVertexClient` wrapper. |
| `/home/user/MangoMas_V2/tests/vertex/test_summarize_invoke.py` | new | Parity of summarize scenario. |
| `/home/user/MangoMas_V2/tests/vertex/test_unknown_model.py` | new | Parity of error envelope scenario (bad model id → `LLMBadResponse` → 502). |
| `/home/user/MangoMas_V2/tests/vertex/test_smoke.py` | new | `ping()` smoke. |
| `/home/user/MangoMas_V2/tests/test_vertex.py` | new | Unit tests using `respx`-style HTTP mocking against `google.api_core.client_options` is brittle; instead, monkeypatch `google.cloud.aiplatform.GenerativeModel` at the import site to a stub that returns canned responses. Verify error translation, streaming chunk pass-through, `ping()` behaviour. |
| `/home/user/MangoMas_V2/tests/conftest.py` | modify | Add `RUN_VERTEX=1` gate in `pytest_collection_modifyitems` mirroring the existing `RUN_LMSTUDIO` block (lines 35–44). |
| `/home/user/MangoMas_V2/tests/constants.py` | modify | Add `VERTEX_PROJECT_ID_ENV`, `VERTEX_LOCATION_ENV`, `VERTEX_MODEL_ENV` env-var names. |
| `/home/user/MangoMas_V2/scripts/check_coverage.py` | modify | The `Floor("src/mangomas/adapters/**/*.py", 85, "adapters")` already covers `vertex.py`; no new floor required, but verify after first run. |
| `/home/user/MangoMas_V2/docs/testing/lmstudio-e2e.md` | modify | Add a "Vertex parity suite" cross-link section. |
| `/home/user/MangoMas_V2/docs/testing/vertex-e2e.md` | new | Mirror of `lmstudio-e2e.md` with Vertex coordinates and `RUN_VERTEX=1`. |

### Settings additions.
| Env var | Settings field | Default | Validation |
|---|---|---|---|
| `MANGOMAS_LLM__PROVIDER` | `LLMSettings.provider` | `"lmstudio"` | Existing field; `"vertex"` now a valid value. |
| `MANGOMAS_LLM__GCP_PROJECT_ID` | `LLMSettings.gcp_project_id` | `None` | `str \| None`. Required when `provider == "vertex"` — Vertex factory raises `ConfigError` if `None`. |
| `MANGOMAS_LLM__GCP_LOCATION` | `LLMSettings.gcp_location` | `None` | `str \| None`. Required when `provider == "vertex"`. |
| `MANGOMAS_LLM__GCP_CREDENTIALS_PATH` | `LLMSettings.gcp_credentials_path` | `None` | `str \| None`. When `None`, `google.auth.default()` discovers ambient creds (Workload Identity, ADC, metadata server). |
| `MANGOMAS_LLM__MODEL` | `LLMSettings.model` | `"local-model"` | Existing field; for Vertex this is the Gemini model id (e.g. `"gemini-1.5-pro-002"`). No hardcoded model name in source — comes from settings. |
| `MANGOMAS_LLM__TIMEOUT_SECONDS` | `LLMSettings.timeout_seconds` | `60.0` | Existing; reused for Vertex per-request timeout. |
| `MANGOMAS_LLM__TEMPERATURE` | `LLMSettings.temperature` | `0.2` | Existing; reused. |

### Registry wiring.
At the bottom of the seed block in `composition.py` (after line 96):

```python
try:
    from mangomas.adapters.llm.vertex import build_vertex_client
    llm_registry.register("vertex", build_vertex_client)
except ImportError:
    logger.debug("Vertex provider not registered: google-cloud-aiplatform not installed")
```

Where `build_vertex_client(cfg: LLMSettings) -> VertexLLMClient` is the new factory exposed from `vertex.py`. The `try/except ImportError` keeps the base install valid; absence of the extra is logged at DEBUG, not warned. If a user sets `MANGOMAS_LLM__PROVIDER=vertex` without installing `mangomas[gcp]`, `llm_registry.get("vertex")` raises `UnknownProvider` (existing 400 mapping).

### Logging touchpoints.
- `VertexLLMClient.__init__` — `INFO`: `"Vertex client initialized"` with `extra={"project": project_id, "location": location, "model": model}`. (No api_key in the extra dict — Vertex uses ambient creds.)
- Retry / 429 backoff (if implemented via `google.api_core.retry`) — `WARNING`: `"Vertex request retrying"` with `extra={"attempt": n, "model": model}`.
- Error paths — `ERROR`: `"Vertex request failed"` with `extra={"error": type(exc).__name__, "model": model, "operation": "complete" | "stream" | "ping"}`. Mirrors the LM Studio pattern at `lmstudio.py:77-80`.
- `VertexLLMClient.aclose` — `DEBUG`: `"Vertex client closed"`.

### Error model.
No new `MangomasError` subclasses. `_translate_vertex_error` (private to `vertex.py`) maps:
- `google.api_core.exceptions.DeadlineExceeded` → `LLMTimeout`
- `google.api_core.exceptions.ServiceUnavailable` / `Unavailable` / `RetryError` → `LLMUnavailable`
- `google.api_core.exceptions.InvalidArgument` / `NotFound` / `PermissionDenied` → `LLMBadResponse`
- Any other `GoogleAPIError` → `LLMUnavailable` with the original message in `detail`.

No `_ERROR_STATUS` changes — these all map to existing entries (502 / 503 / 504).

### Test strategy.
- **Unit (`tests/test_vertex.py`).**
  - Use a `class FakeGenerativeModel:` injected via `monkeypatch.setattr(vertex, "_get_model", ...)` to canned `generate_content` / `generate_content_async` / `count_tokens` returns. Avoid actual SDK calls.
  - Cases: complete happy path, complete error → `LLMBadResponse`, streaming yields N chunks, streaming error mid-stream, ping success, ping failure.
  - Hypothesis: `@given(strategies.lists(message_strategy, min_size=1))` against `complete()` — verifies arbitrary message lists serialise without `KeyError`. Existing pattern in `tests/test_tools.py`.
- **Integration (ASGI).** `tests/integration/test_api_flow_vertex.py` — gated on `RUN_VERTEX=1`, exercises `/agents/chat/invoke` against a real Vertex endpoint, asserts 200 + non-empty content + persisted turn. Parity with `test_api_flow.py`.
- **Parity / E2E (`tests/vertex/`).** Five scenarios from `tests/lmstudio/` must pass identically against Vertex, gated on `RUN_VERTEX=1`. The parity gate **is** the proof that `LLMClient` is a true seam.

### Backwards-compatibility checklist.
- `MANGOMAS_LLM__PROVIDER` defaults to `"lmstudio"`; existing deployments unchanged.
- New `LLMSettings` fields default to `None` — `LLMSettings()` still constructs identically.
- `_lmstudio_factory` unchanged.
- `FakeLLM` unchanged (already satisfies all three protocols).
- `tests/lmstudio/` unchanged.
- Existing tests that monkeypatch `llm_registry` continue to work (no API changes).
- Deprecation notice: none.

### Acceptance criteria.
- `MANGOMAS_LLM__PROVIDER=vertex` + `MANGOMAS_LLM__GCP_PROJECT_ID=...` + `MANGOMAS_LLM__GCP_LOCATION=...` + `pip install mangomas[gcp]` produces a working chat invoke + stream against Vertex.
- All five `tests/vertex/test_*.py` scenarios pass with `RUN_VERTEX=1`.
- `isinstance(client, StreamingLLMClient)` returns `True`.
- `isinstance(client, PingableLLMClient)` returns `True`.
- Vertex parity test for buffered-fallback (`test_stream_fallback.py`) emits the `_FALLBACK_WARNING_MESSAGE` log line — proving the shared streaming helper covers Vertex too.
- Unit-test coverage ≥ 85 % on `vertex.py`.
- mypy --strict passes on the new module.

### Risk + mitigations.
- **Risk:** `google.cloud.aiplatform` is a heavy SDK; per-test import cost balloons CI runtime. **Mitigation:** lazy import inside the factory; unit tests monkeypatch the model class without importing the SDK.
- **Risk:** SDK async API drifts between versions. **Mitigation:** pin `>=1.60,<2.0`; integration suite catches breakage.
- **Risk:** Vertex streaming chunks arrive as `GenerateContentResponse` objects that need `.text` extraction; an off-by-one or empty-token bug breaks the SSE envelope. **Mitigation:** parity test `test_chat_stream.py` asserts at least one non-empty token (mirrors `lmstudio/test_chat_stream.py:53`).

---

## Milestone M4 — GCP Secret Manager Backend

### Objective.
Ship a `GcpSecretManagerProvider` satisfying `SecretsProvider`, registered as `secrets_registry.register("gcp", ...)`, activatable via `MANGOMAS_SECRETS__PROVIDER=gcp`, so `LLMSettings.secret_ref` resolves through Google Secret Manager when configured.

### Dependency edge.
Requires M1 baseline. Can run in parallel with M3 (no file overlap except `composition.py` and `pyproject.toml::[project.optional-dependencies].gcp`). Consumes:
- `SecretsProvider` protocol at `/home/user/MangoMas_V2/src/mangomas/secrets/provider.py:19`.
- `secrets_registry` at `/home/user/MangoMas_V2/src/mangomas/secrets/registry.py:14`.
- `_resolve_llm_secrets` consumer at `composition.py:54`.
- `FakeSecretsProvider` in `tests/fakes.py:134` — already used by `tests/test_secrets.py`.

### Gap analysis.
- **Exists.**
  - `SecretsProvider` protocol — sync `get(name: str) -> str | None`. Per `provider.py:11`: "The protocol is intentionally sync-only for v0.2.0; cloud backends with async APIs can be added without a breaking change later".
  - `secrets_registry` seeded with `EnvSecretsProvider` at import time.
  - `_resolve_llm_secrets(llm_cfg, secrets_provider_name)` already routes through the registry.
  - `FakeSecretsProvider` for unit testing the consumer (already in `tests/fakes.py:134`).
- **Missing.**
  - `src/mangomas/secrets/gcp.py` — `GcpSecretManagerProvider` class implementing `get(name: str) -> str | None`.
  - Registration block in `secrets/__init__.py` or a new `secrets/_setup_gcp.py` that tries the import and registers — gated by ImportError swallow.
  - `MANGOMAS_SECRETS__GCP_PROJECT_ID` env var via `SecretsSettings`.
  - Optional dep `google-cloud-secret-manager` in `[project.optional-dependencies].gcp` (the same extra introduced by M2 — confirmed parallel-merge-safe).
  - Unit tests in `tests/test_secrets_gcp.py`.
  - Per-package floor for `src/mangomas/secrets/gcp.py` — existing `Floor("src/mangomas/secrets/*.py", 100, "secrets")` already covers it.
- **Redundant-if-added.**
  - Do **not** add an async `SecretsProvider.aget` variant — provider note line 11 says sync is intentional; GCP SDK's sync calls run in `asyncio.to_thread` at the `_resolve_llm_secrets` call site if needed. Resolution happens once, at build time, so blocking is acceptable.
  - Do **not** add a new "secret caching" layer — Secret Manager's local in-process cache via `SecretManagerServiceClient.access_secret_version` is sufficient at startup; rotation is a Phase-4 concern.
  - Do **not** add a new `LLMSettings.gcp_secrets_project` field — reuse the project id from `MANGOMAS_SECRETS__GCP_PROJECT_ID` (sibling of `MANGOMAS_LLM__GCP_PROJECT_ID` — see Open Questions for whether they should default to the same).

### File-level plan.
| File | New/Modify | Purpose |
|---|---|---|
| `/home/user/MangoMas_V2/src/mangomas/secrets/gcp.py` | new | `GcpSecretManagerProvider` class. `get(name)` calls `client.access_secret_version(name=name)` where `name` is the full resource path `projects/<id>/secrets/<key>/versions/<ver>` OR a bare key (in which case the provider prepends `projects/<gcp_project_id>/secrets/<key>/versions/latest`). Lazy SDK import inside `__init__`. |
| `/home/user/MangoMas_V2/src/mangomas/secrets/__init__.py` | modify | Conditional re-export of `GcpSecretManagerProvider` (ImportError-swallowed). |
| `/home/user/MangoMas_V2/src/mangomas/secrets/registry.py` | modify | Try-register `"gcp"` factory if the optional extra is installed; deferred-registration pattern (a small `_register_optional_gcp()` function called at the bottom of the module). |
| `/home/user/MangoMas_V2/src/mangomas/config.py` | modify | Extend `SecretsSettings` with `gcp_project_id: str \| None = None`. |
| `/home/user/MangoMas_V2/src/mangomas/composition.py` | modify | No change to `_resolve_llm_secrets` — it already routes via `secrets_provider_name`. Add a one-line `logger.debug(...)` if `cfg.secrets.provider == "gcp"` confirming the GCP provider was selected. |
| `/home/user/MangoMas_V2/pyproject.toml` | modify | Add `google-cloud-secret-manager>=2.20` to `[project.optional-dependencies].gcp`. |
| `/home/user/MangoMas_V2/tests/test_secrets_gcp.py` | new | Unit tests: provider returns value, returns `None` for not-found, raises typed error on permission denied; SDK mocked via `monkeypatch.setattr`. |
| `/home/user/MangoMas_V2/tests/test_composition.py` | modify | Add a test using `secrets_registry.scoped("gcp", FakeSecretsProvider(...))` to verify `_resolve_llm_secrets` consumes the GCP provider when `cfg.secrets.provider == "gcp"`. |
| `/home/user/MangoMas_V2/tests/fakes.py` | unchanged | `FakeSecretsProvider` already exists; reused. |
| `/home/user/MangoMas_V2/scripts/check_coverage.py` | unchanged | Existing 100 % floor on `src/mangomas/secrets/*.py` covers `gcp.py`. |
| `/home/user/MangoMas_V2/docs/architecture/c3-component.md` | modify | Add `GcpSecretManagerProvider` node to the Secrets boundary block. |

### Settings additions.
| Env var | Settings field | Default | Validation |
|---|---|---|---|
| `MANGOMAS_SECRETS__PROVIDER` | `SecretsSettings.provider` | `"env"` | Existing field; `"gcp"` becomes a valid value. |
| `MANGOMAS_SECRETS__GCP_PROJECT_ID` | `SecretsSettings.gcp_project_id` | `None` | `str \| None`. Required when `provider == "gcp"` AND `secret_ref` is a bare key (not a fully-qualified resource path). |

### Registry wiring.
In `secrets/registry.py`, after the existing `secrets_registry.register("env", ...)` line:

```python
def _register_optional_gcp() -> None:
    try:
        from mangomas.secrets.gcp import GcpSecretManagerProvider
    except ImportError:
        logger.debug("GCP Secret Manager provider not registered: SDK not installed")
        return
    secrets_registry.register("gcp", GcpSecretManagerProvider())

_register_optional_gcp()
```

The provider is **stateless** at instantiation — it constructs the client lazily on first `get()`. This keeps base install free of SDK imports.

### Logging touchpoints.
- `GcpSecretManagerProvider.__init__` — `INFO` only on first lazy client construction (not on init itself): `"GCP Secret Manager client initialized"` with `extra={"project": project_id}`.
- `get()` success — `DEBUG`: `"Resolved secret"` with `extra={"name_hash": sha256(name)[:8]}` (never log the resource name verbatim — could include sensitive path info).
- `get()` `PermissionDenied` → `ERROR`: `"Permission denied accessing secret"` with `extra={"name_hash": sha256(name)[:8]}`. Returns `None` (consumer falls back to inline api_key per `composition.py:67`).
- `get()` `NotFound` → `WARNING`: `"Secret not found"` returns `None`.

### Error model.
No new exception classes. The provider returns `None` for not-found / permission-denied (matching `EnvSecretsProvider` semantics — `composition.py:67` already handles `None`). Unexpected SDK errors (network, auth) propagate as `google.api_core.exceptions.GoogleAPIError` and are caught at the `_resolve_llm_secrets` call site — wrap into `ConfigError` if startup must fail loudly. **Decision needed (see Open Questions):** "Should provider exceptions surface as `ConfigError` or be swallowed?" Current proposal: swallow + log, mirroring `EnvSecretsProvider`'s never-fail contract.

### Test strategy.
- **Unit.**
  - `tests/test_secrets_gcp.py` — monkeypatch `google.cloud.secretmanager.SecretManagerServiceClient` to a stub. Cases: bare key + project resolves to fully-qualified path; fully-qualified path used verbatim; not-found → `None`; permission-denied → `None` + ERROR log; client construction lazy.
  - Use `FakeSecretsProvider` from `tests/fakes.py` to test `_resolve_llm_secrets` consumer path — no SDK touch.
- **Integration.** Optional `RUN_GCP_SECRETS=1` test reads a real secret; doc-only, gated like other RUN_* flags.
- **Parity / E2E.** Not applicable.

### Backwards-compatibility checklist.
- `MANGOMAS_SECRETS__PROVIDER` defaults to `"env"`; existing deployments unaffected.
- `EnvSecretsProvider` unchanged.
- `LLMSettings.secret_ref` semantics unchanged — bare string passed to whichever provider is configured.
- Deprecation notice: none.

### Acceptance criteria.
- `MANGOMAS_SECRETS__PROVIDER=gcp` + `MANGOMAS_SECRETS__GCP_PROJECT_ID=p` + `MANGOMAS_LLM__SECRET_REF=vertex-key` + `pip install mangomas[gcp]` resolves the secret at orchestrator-build time and replaces `LLMSettings.api_key`.
- Without the extra installed, `MANGOMAS_SECRETS__PROVIDER=gcp` raises `UnknownProvider` (400) at build time — clean error.
- Unit tests for the provider hit 100 % coverage on `src/mangomas/secrets/gcp.py`.
- mypy --strict passes.

### Risk + mitigations.
- **Risk:** Secret name format ambiguity (bare key vs fully-qualified path). **Mitigation:** branch on `name.startswith("projects/")`; both forms tested.
- **Risk:** Workload Identity not configured in local dev. **Mitigation:** unit tests mock the SDK entirely; integration test is opt-in.

---

## Milestone M5 — PostgresRepository

### Objective.
Ship `PostgresRepository` satisfying `TurnRepository`, registered as `_storage_registry.register("postgres", ...)`, activatable via `MANGOMAS_DB__PROVIDER=postgres`, with parity tests gated on `RUN_POSTGRES=1`.

### Dependency edge.
Requires M1 baseline. Benefits from M2 (Cloud Trace) for span propagation through DB calls. Independent of M3/M4. Consumes:
- `TurnRepository` protocol at `/home/user/MangoMas_V2/src/mangomas/adapters/storage/base.py:16`.
- `_storage_registry` at `composition.py:46`.
- `AgentRequest` / `AgentResponse` Pydantic models (`save_turn` payload).
- `SQLiteRepository._SCHEMA` (`sqlite.py:50-58`) as the reference schema (translate to Postgres types: `SERIAL PRIMARY KEY`, `TIMESTAMPTZ`, `TEXT`).

### Gap analysis.
- **Exists.**
  - `TurnRepository` protocol — `save_turn`, `list_turns`, `close` async signatures.
  - `SQLiteRepository` reference implementation (`sqlite.py:47-123`) for schema and behaviour parity.
  - `FakeRepository` for unit tests (`tests/fakes.py:86`).
  - `Settings.db.url` already a generic URL string — Postgres URLs (`postgresql://...` / `postgresql+asyncpg://...`) parse identically through pydantic.
  - `asyncio.to_thread` is **not** needed if asyncpg is used (already async); for `psycopg3` async mode it's also not needed.
- **Missing.**
  - `src/mangomas/adapters/storage/postgres.py` — async implementation with connection pooling.
  - `_postgres_factory` in `composition.py` with lazy import.
  - Optional dep `asyncpg>=0.29` (recommended) or `psycopg[binary]>=3.2` (alternative — see Open Questions) in a new `[project.optional-dependencies].postgres` extra.
  - `tests/postgres/` parity suite with `conftest.py` + scenario files.
  - `RUN_POSTGRES=1` gate in `tests/conftest.py`.
  - `postgres` pytest marker.
  - Per-package coverage floor — covered by existing `Floor("src/mangomas/adapters/**/*.py", 85, "adapters")`.
  - DB schema migration story — **deferred to a follow-up**; v0.4.0 ships idempotent `CREATE TABLE IF NOT EXISTS`, matching SQLite behaviour (`sqlite.py:50`).
- **Redundant-if-added.**
  - Do **not** add a `MemoryRepository` Postgres backend in this milestone — episodic memory is a separate seam; ship later if needed.
  - Do **not** add an ORM (SQLAlchemy) — `SQLiteRepository` uses raw SQL; Postgres mirrors that to avoid a heavy dep.
  - Do **not** add a new `PersistenceError` subclass — existing `PersistenceError` (`errors.py:105`) suffices.

### File-level plan.
| File | New/Modify | Purpose |
|---|---|---|
| `/home/user/MangoMas_V2/src/mangomas/adapters/storage/postgres.py` | new | `PostgresRepository` class. asyncpg pool created in `__init__` (eagerly opened); `save_turn`, `list_turns`, `close` (async if asyncpg, sync wrapper to await pool close). Schema:`CREATE TABLE IF NOT EXISTS turns (id SERIAL PRIMARY KEY, ts TIMESTAMPTZ NOT NULL, agent TEXT NOT NULL, request JSONB NOT NULL, response JSONB NOT NULL)`. Use `JSONB` (not `TEXT`) so future queries can index. |
| `/home/user/MangoMas_V2/src/mangomas/adapters/storage/__init__.py` | modify | Conditional re-export of `PostgresRepository` with ImportError swallow. |
| `/home/user/MangoMas_V2/src/mangomas/composition.py` | modify | `_postgres_factory(cfg: DBSettings) -> PostgresRepository`; `_storage_registry.register("postgres", _postgres_factory)` guarded by try/except ImportError. **Lifespan note:** `PostgresRepository.close` must be awaitable; check `api/app.py:88` — currently calls `ctx.repo.close()` synchronously. Postgres adapter exposes a `close()` sync method that calls `asyncio.get_event_loop().run_until_complete(pool.close())` OR — cleaner — promotes `TurnRepository.close()` to optionally async (backwards-compat: SQLite remains sync, Postgres detects `inspect.iscoroutinefunction` at the lifespan call site). Recommendation: extend the lifespan check at `app.py:88` to `await ctx.repo.close()` when awaitable, mirroring the `ctx.llm.aclose` pattern at line 86. This is a one-line change, not a protocol change. |
| `/home/user/MangoMas_V2/src/mangomas/adapters/storage/base.py` | modify | Document in docstring that `close()` may be sync OR async; provide a `Closeable` runtime check helper to keep the consumer side clean. Protocol itself unchanged — `close()` returns `None`, asyncpg's `pool.close()` also returns a coroutine that returns `None`. |
| `/home/user/MangoMas_V2/src/mangomas/api/app.py` | modify | Lifespan: change `ctx.repo.close()` to `await ctx.repo.close() if inspect.iscoroutinefunction(ctx.repo.close) else ctx.repo.close()`. One line + import. |
| `/home/user/MangoMas_V2/pyproject.toml` | modify | Add `[project.optional-dependencies].postgres = ["asyncpg>=0.29"]`. Add `postgres` pytest marker. |
| `/home/user/MangoMas_V2/tests/postgres/__init__.py` | new | Package marker. |
| `/home/user/MangoMas_V2/tests/postgres/conftest.py` | new | `postgres_dsn` fixture (reads `POSTGRES_DSN` env var or skips); `postgres_repo` fixture (creates a temporary schema, yields, drops); mirrors `tests/lmstudio/conftest.py` style. |
| `/home/user/MangoMas_V2/tests/postgres/test_save_list_roundtrip.py` | new | Round-trip test: save N turns, list, assert order + JSON equality. |
| `/home/user/MangoMas_V2/tests/postgres/test_concurrent_writes.py` | new | Concurrent `save_turn` from multiple tasks — verifies asyncpg pool serialisation, matching the SQLite lock test pattern. |
| `/home/user/MangoMas_V2/tests/postgres/test_api_flow.py` | new | Parity of `tests/integration/test_api_flow.py` against Postgres. |
| `/home/user/MangoMas_V2/tests/test_postgres.py` | new | Unit tests against a stubbed `asyncpg.Pool` — verifies SQL formation, error translation to `PersistenceError`. |
| `/home/user/MangoMas_V2/tests/conftest.py` | modify | Add `RUN_POSTGRES=1` gate. |
| `/home/user/MangoMas_V2/tests/constants.py` | modify | Add `POSTGRES_DSN_ENV = "POSTGRES_DSN"`. |
| `/home/user/MangoMas_V2/docs/testing/postgres-e2e.md` | new | Test plan + DSN format + docker-compose snippet for local Postgres. |
| `/home/user/MangoMas_V2/docker-compose.yml` | modify | Add an optional `postgres` service block (commented out by default) so developers can `docker compose up postgres` for local testing. |

### Settings additions.
| Env var | Settings field | Default | Validation |
|---|---|---|---|
| `MANGOMAS_DB__PROVIDER` | `DBSettings.provider` | `"sqlite"` | Existing; `"postgres"` becomes valid. |
| `MANGOMAS_DB__URL` | `DBSettings.url` | `"sqlite:///./data/mangomas.db"` | Existing; Postgres URLs (`postgresql://user:pass@host:5432/db`) parse identically. |
| `MANGOMAS_DB__POOL_MIN_SIZE` | `DBSettings.pool_min_size` | `1` | `int >= 0`, asyncpg pool floor. |
| `MANGOMAS_DB__POOL_MAX_SIZE` | `DBSettings.pool_max_size` | `10` | `int >= 1`, asyncpg pool ceiling. |
| `MANGOMAS_DB__STATEMENT_TIMEOUT_SECONDS` | `DBSettings.statement_timeout_seconds` | `30.0` | `float > 0`, per-query timeout. |

### Registry wiring.
```python
try:
    from mangomas.adapters.storage.postgres import PostgresRepository
    def _postgres_factory(cfg: DBSettings) -> PostgresRepository:
        return PostgresRepository(
            dsn=cfg.url,
            pool_min_size=cfg.pool_min_size,
            pool_max_size=cfg.pool_max_size,
            statement_timeout_seconds=cfg.statement_timeout_seconds,
        )
    _storage_registry.register("postgres", _postgres_factory)
except ImportError:
    logger.debug("Postgres provider not registered: asyncpg not installed")
```

### Logging touchpoints.
- `PostgresRepository.__init__` — `INFO`: `"Postgres pool created"` with `extra={"dsn_host": parsed.host, "dsn_db": parsed.database, "pool_min": min, "pool_max": max}`. Never log the password.
- `save_turn` failure — `ERROR`: `"Postgres write failed"` with `extra={"agent": agent_name, "error": type(exc).__name__}`. Wrap into `PersistenceError`.
- `list_turns` failure — same pattern.
- Connection reconnect (asyncpg auto-reconnects) — `WARNING`: `"Postgres connection lost; pool reconnecting"`.
- `close` — `DEBUG`: `"Postgres pool closed"`.

### Error model.
- Existing `PersistenceError` reused. asyncpg's `PostgresError` subtree is caught at the boundary and wrapped:
  - `asyncpg.exceptions.ConnectionFailureError` → `PersistenceError(..., detail="connection_lost")`
  - `asyncpg.exceptions.PostgresError` → `PersistenceError(..., detail=type(exc).__name__)`
- No new `_ERROR_STATUS` entry — `PersistenceError` already maps to 500.

### Test strategy.
- **Unit (`tests/test_postgres.py`).**
  - Stub `asyncpg.create_pool` and verify the repository constructs the expected pool args.
  - Stub the pool to return a fake connection; verify SQL strings emitted to `connection.execute` / `fetch`.
  - Error translation: `asyncpg.exceptions.PostgresError` → `PersistenceError`.
- **Integration (`tests/postgres/`).** Requires a live Postgres on `POSTGRES_DSN`. Use a unique schema name per test session to enable parallel CI runs.
- **Parity / E2E.** `tests/postgres/test_api_flow.py` replays the full agent dispatch against Postgres, verifying that the `TurnRepository` swap is invisible to upstream code.

### Backwards-compatibility checklist.
- `MANGOMAS_DB__PROVIDER` defaults to `"sqlite"`; existing deployments unaffected.
- `SQLiteRepository` unchanged; pool/timeout fields on `DBSettings` are ignored by the SQLite factory.
- `app.py` lifespan: existing sync-close path preserved via the `iscoroutinefunction` branch.
- Deprecation notice: none.

### Acceptance criteria.
- `MANGOMAS_DB__PROVIDER=postgres MANGOMAS_DB__URL=postgresql://...` + `pip install mangomas[postgres]` produces a working orchestrator that persists turns to Postgres.
- All `tests/postgres/` scenarios pass with `RUN_POSTGRES=1`.
- `tests/test_api_flow.py`-shaped integration test passes against Postgres.
- Concurrent dispatches via `dispatch_fan_out` do not deadlock or corrupt data (parity with the SQLite-lock test added in v0.1.0 — `CHANGELOG.md:17`).
- mypy --strict passes; coverage ≥ 85 %.

### Risk + mitigations.
- **Risk:** asyncpg vs psycopg3 choice locks us in. **Mitigation:** asyncpg's API is wrapped behind `PostgresRepository`; swap is local. Open Question recorded.
- **Risk:** `close()` sync-vs-async mismatch in lifespan. **Mitigation:** the `iscoroutinefunction` branch is small and tested.
- **Risk:** Schema drift between SQLite and Postgres (e.g. `TEXT` vs `JSONB`). **Mitigation:** identical round-trip tests against both backends in the parity suite. Migrations deferred to a follow-up milestone.

---

## Milestone M6 — Cloud Run Deployment Pipeline

### Objective.
Add a `deploy/` directory with a Cloud Run service YAML, a GitHub Actions workflow that builds and pushes the image to Artifact Registry, and an env-var contract document — without touching the existing `ci.yml`.

### Dependency edge.
Requires M2, M3, M4, M5 — the env-var contract documented in the workflow must include the full Cloud Trace / Vertex / Secret Manager / Postgres set. Consumes:
- `Dockerfile` at `/home/user/MangoMas_V2/Dockerfile` (already non-root, `$PORT`-aware, has `HEALTHCHECK` against `/healthz`).
- `docker-compose.yml` for local parity.
- `.github/workflows/ci.yml` as the reference workflow shape.

### Gap analysis.
- **Exists.**
  - `Dockerfile` — already production-ready per ADR-001 row 5 ("Keep container non-root, `$PORT` aware, and probe `/healthz`").
  - `ci.yml` — lint + test workflow runs on every push.
  - `MANGOMAS_*` env-var convention.
  - `/healthz` and `/readyz` routes wired in `app.py:127-138`.
- **Missing.**
  - `deploy/` directory.
  - `deploy/cloud-run.yaml` — Cloud Run service manifest (or alternatively a Terraform module — see Open Questions).
  - `.github/workflows/deploy.yml` — separate workflow, triggered on `release: published` (matching the v0.2.0 tag convention from M1), that:
    1. Builds the image using the existing `Dockerfile`.
    2. Authenticates to GCP via Workload Identity Federation (no static service-account keys).
    3. Pushes to Artifact Registry.
    4. Deploys to Cloud Run via `gcloud run deploy --image=...`.
  - `deploy/README.md` — env-var contract for Cloud Run (every `MANGOMAS_*` var added in M2–M5, with whether it's required / optional).
  - Documentation cross-link from `docs/adr/0001-cloud-targets.md` Row 5.
- **Redundant-if-added.**
  - Do **not** extend `ci.yml` with a build/push step — keep CI fast; deploy is a separate workflow gated on releases.
  - Do **not** introduce a Helm chart — Cloud Run is the target; YAML is sufficient.
  - Do **not** build a new health endpoint — `/healthz` (200 always when process up) + `/readyz` (aggregates LLM ping + DB probe) already cover liveness + readiness probes that Cloud Run expects.

### File-level plan.
| File | New/Modify | Purpose |
|---|---|---|
| `/home/user/MangoMas_V2/deploy/cloud-run.yaml` | new | `kind: Service` declarative YAML with env vars sourced from Secret Manager + Vertex/Postgres/Telemetry settings. Image placeholder `{{IMAGE}}` substituted by workflow. |
| `/home/user/MangoMas_V2/deploy/README.md` | new | Env-var contract, deployment runbook, rollback procedure, Workload Identity setup pointers. |
| `/home/user/MangoMas_V2/.github/workflows/deploy.yml` | new | `on: release: {types: [published]}`. Jobs: `build-image`, `push-to-artifact-registry`, `deploy-cloud-run`. Uses `google-github-actions/auth@v2` with Workload Identity Federation. Reads target project / region from GitHub repository variables (no secrets in the workflow file). |
| `/home/user/MangoMas_V2/.github/workflows/ci.yml` | unchanged | Keep CI fast. |
| `/home/user/MangoMas_V2/Dockerfile` | possibly modify | Confirm that `pip install` includes the `[gcp,postgres]` extras for the production image. Add a build-arg `MANGOMAS_EXTRAS` defaulting to `"gcp,postgres"`. |
| `/home/user/MangoMas_V2/docs/adr/0001-cloud-targets.md` | modify | Update Row 5 "Compute" with link to `deploy/README.md`. |
| `/home/user/MangoMas_V2/docker-compose.yml` | unchanged | Stays as the local dev story. |
| `/home/user/MangoMas_V2/README.md` | modify | Add "Deploying to Cloud Run" cross-link. |

### Settings additions.
None — M6 documents what M2–M5 produce; it adds no new `MANGOMAS_*` env vars.

### Registry wiring.
None.

### Logging touchpoints.
None at code level. Workflow steps emit standard GHA logs; nothing in source.

### Error model.
None.

### Test strategy.
- **Unit.** Lint the YAML: `yamllint deploy/cloud-run.yaml` step in `ci.yml`. Validate the workflow with `actionlint` — add to existing lint job.
- **Integration.** A "dry-run" deploy on PR: `gcloud run services replace --dry-run` against a non-existent project (validates manifest syntax).
- **Parity / E2E.** Manual smoke after first release: `gh workflow run deploy.yml -f tag=v0.4.0`; verify Cloud Run revision is healthy and `/readyz` returns 200.

### Backwards-compatibility checklist.
- No code changes to `src/`.
- `ci.yml` untouched.
- Existing local dev (`uvicorn`, `docker-compose`) untouched.
- Deprecation notice: none.

### Acceptance criteria.
- `deploy/cloud-run.yaml` validated against the Cloud Run schema (via `gcloud run services replace --dry-run`).
- `deploy.yml` workflow runs to green on a synthetic release.
- `deploy/README.md` lists every `MANGOMAS_*` env var added in M2–M5, with required/optional flagged.
- No service-account JSON keys committed.

### Risk + mitigations.
- **Risk:** Workload Identity Federation setup is operator-side (outside repo); workflow fails on first attempt. **Mitigation:** `deploy/README.md` includes a one-time WIF bootstrap section with `gcloud iam workload-identity-pools create` commands.
- **Risk:** Image size balloons due to `gcp,postgres` extras. **Mitigation:** existing multi-stage Dockerfile (`Dockerfile:1-13` builder stage) keeps the final image lean; verify final size < 500 MB as a build-time check.

---

## Milestone M7 — Evaluation Harness

### Objective.
Ship an offline evaluation harness that runs agent responses against a dataset of expected input/output pairs, scored by a pluggable `Scorer` protocol (exact-match, embedding-similarity, LLM-as-judge); gated on `RUN_EVALS=1`.

### Dependency edge.
Parallelizable with M3–M6. Requires M1 baseline only. Does not consume cloud SDKs in the base scorer set — the `LLMAsJudgeScorer` (a future scorer) uses whatever `LLMClient` is configured, which could be Vertex (post-M3) or LM Studio. Consumes:
- `AgentResponse.content` shape (`core/agent.py:37`).
- `Orchestrator.dispatch` (the harness invokes agents through the same orchestrator the API uses).
- `Settings` + `build_orchestrator` for end-to-end runs.

### Gap analysis.
- **Exists.**
  - `Orchestrator` already exposes a single-call `dispatch(name, request)` (`composition.py:130`).
  - `AgentResponse.content: str` for comparison.
  - `Settings` machinery for env-driven dataset paths.
  - `FakeLLM` with deterministic `reply` for unit-testing the harness without a real LLM.
- **Missing — and the only milestone that justifies a new protocol.**
  - `src/mangomas/evals/` package.
  - `src/mangomas/evals/scorer.py` — `Scorer` protocol with `score(expected: str, actual: str, *, request: AgentRequest) -> ScoreResult` (`ScoreResult` carries `score: float`, `passed: bool`, `detail: str`).
  - **Justification for a new protocol:** none of the existing protocols (`Agent`, `LLMClient`, `TurnRepository`, `SecretsProvider`) accept `(expected, actual)` semantics. Reusing `Agent` would conflate "runs an agent" with "scores its output". The `Scorer` surface is genuinely new and minimal (one method).
  - `src/mangomas/evals/exact_match.py` — `ExactMatchScorer`, the simplest reference implementation.
  - `src/mangomas/evals/llm_judge.py` — `LLMAsJudgeScorer` that uses the configured `LLMClient` to score (`Scorer.llm` field set at construction).
  - `src/mangomas/evals/embedding.py` — placeholder for `EmbeddingSimilarityScorer`; ship with a stub raising `NotImplementedError` and an `__all__` opt-in, OR defer to a separate ticket.
  - `src/mangomas/evals/runner.py` — `EvalRunner` that loads a dataset (JSONL or Parquet — see Open Questions), iterates through cases, dispatches via `Orchestrator`, scores, and emits a structured report (JSON envelope to stdout + optional Cloud Storage upload).
  - `src/mangomas/evals/dataset.py` — `EvalCase` Pydantic model (`request: AgentRequest`, `expected: str`, `metadata: dict`). Dataset loader for JSONL.
  - `scorer_registry: Registry[Callable[..., Scorer]]` in `src/mangomas/evals/__init__.py` — mirrors `llm_registry` pattern.
  - `evals` CLI subcommand under `mangomas.cli.main`.
  - Optional dep `pyarrow>=15` (only if Parquet is the chosen format) in `[project.optional-dependencies].evals`.
  - `tests/evals/` parity suite (unit + integration).
  - `RUN_EVALS=1` pytest gate.
  - Per-package coverage floor for `src/mangomas/evals/*.py` — propose 90 %.
- **Redundant-if-added.**
  - Do **not** repurpose `Agent.handle` as a scorer — the (expected, actual, request) signature is incompatible.
  - Do **not** add a new `Orchestrator` method like `evaluate` — the harness invokes the existing `dispatch` per case; no orchestrator changes.
  - Do **not** add a new `MangomasError` subclass for "case failed" — case failure is a value (`ScoreResult.passed = False`), not an exception.

### File-level plan.
| File | New/Modify | Purpose |
|---|---|---|
| `/home/user/MangoMas_V2/src/mangomas/evals/__init__.py` | new | `Scorer`, `ScoreResult`, `EvalRunner`, `scorer_registry` exports. |
| `/home/user/MangoMas_V2/src/mangomas/evals/scorer.py` | new | `@runtime_checkable Scorer` protocol + `ScoreResult` dataclass. |
| `/home/user/MangoMas_V2/src/mangomas/evals/exact_match.py` | new | `ExactMatchScorer`, `NormalizedExactMatchScorer` (strip + lower). |
| `/home/user/MangoMas_V2/src/mangomas/evals/llm_judge.py` | new | `LLMAsJudgeScorer` consumes `LLMClient`; uses a configurable rubric prompt (default constant in module, override via settings). |
| `/home/user/MangoMas_V2/src/mangomas/evals/embedding.py` | new (stub) | `EmbeddingSimilarityScorer` raising `NotImplementedError` — placeholder for v0.6.x. |
| `/home/user/MangoMas_V2/src/mangomas/evals/dataset.py` | new | `EvalCase` Pydantic model; `load_jsonl(path) -> Iterator[EvalCase]`. |
| `/home/user/MangoMas_V2/src/mangomas/evals/runner.py` | new | `EvalRunner(orchestrator, scorer, dataset)` — async iterator emitting `EvalCaseResult`; aggregate stats (pass rate, latency p50/p95). Logs each case at `INFO`. |
| `/home/user/MangoMas_V2/src/mangomas/cli/main.py` | modify | Add `evals run --dataset PATH --scorer NAME --agent NAME` Typer subcommand. |
| `/home/user/MangoMas_V2/src/mangomas/config.py` | modify | Add `class EvalSettings(BaseModel)` with `scorer: str = "exact_match"`, `dataset_path: str \| None`, `judge_prompt: str \| None`. Attach to `Settings.evals`. |
| `/home/user/MangoMas_V2/pyproject.toml` | modify | Add `evals` pytest marker. (No new dep unless Parquet wins — keep base evals JSONL-only.) |
| `/home/user/MangoMas_V2/tests/evals/__init__.py` | new | Package marker. |
| `/home/user/MangoMas_V2/tests/evals/test_scorer_protocol.py` | new | Protocol conformance: `isinstance(ExactMatchScorer(), Scorer) is True`. |
| `/home/user/MangoMas_V2/tests/evals/test_exact_match.py` | new | Score values 0.0 / 1.0, edge cases. Hypothesis: random strings. |
| `/home/user/MangoMas_V2/tests/evals/test_llm_judge.py` | new | Uses `FakeLLM` from `tests/fakes.py` with canned judge responses; verifies parsing. |
| `/home/user/MangoMas_V2/tests/evals/test_runner.py` | new | Runner over a 3-case JSONL fixture; verifies aggregate stats. Uses `FakeLLM` + `FakeRepository`. |
| `/home/user/MangoMas_V2/tests/evals/fixtures/sample.jsonl` | new | 3 small `EvalCase` examples for tests. |
| `/home/user/MangoMas_V2/tests/conftest.py` | modify | Add `RUN_EVALS=1` gate (optional — unit tests run unconditionally; only end-to-end eval runs gated). |
| `/home/user/MangoMas_V2/scripts/check_coverage.py` | modify | Add `Floor("src/mangomas/evals/*.py", 90, "evals")`. |
| `/home/user/MangoMas_V2/docs/evals.md` | new | Harness overview, dataset format, scorer reference, CLI examples. |

### Settings additions.
| Env var | Settings field | Default | Validation |
|---|---|---|---|
| `MANGOMAS_EVALS__SCORER` | `EvalSettings.scorer` | `"exact_match"` | Must match a registered scorer. |
| `MANGOMAS_EVALS__DATASET_PATH` | `EvalSettings.dataset_path` | `None` | `str \| None`. CLI arg overrides this. |
| `MANGOMAS_EVALS__JUDGE_PROMPT` | `EvalSettings.judge_prompt` | `None` (uses module-level default) | `str \| None`. |
| `MANGOMAS_EVALS__BATCH_SIZE` | `EvalSettings.batch_size` | `1` | `int >= 1`. Future concurrency knob; ship with 1, document. |

### Registry wiring.
A **new** module-level `scorer_registry: Registry[Scorer]` in `src/mangomas/evals/__init__.py`. Seeded with `exact_match` and `llm_judge`. CLI / runner consume via `scorer_registry.get(settings.evals.scorer)`. No changes to `composition.py` — evals do not participate in `build_orchestrator`; they consume the orchestrator after it is built.

### Logging touchpoints.
- `EvalRunner.run` start — `INFO`: `"Eval run starting"` with `extra={"dataset_path": ..., "scorer": ..., "case_count": ..., "agent": ...}`.
- Per-case completion — `INFO`: `"Eval case complete"` with `extra={"case_id": ..., "passed": ..., "score": ..., "latency_ms": ...}`.
- Run summary — `INFO`: `"Eval run complete"` with `extra={"pass_rate": ..., "p50_ms": ..., "p95_ms": ..., "failures": ...}`.

### Error model.
No new exception classes. Dataset load failures surface as `FileNotFoundError` / `json.JSONDecodeError` — wrapped into `ConfigError` at the CLI boundary so the structured envelope still applies.

### Test strategy.
- **Unit.**
  - Protocol conformance for each scorer.
  - Runner against a 3-case JSONL fixture with `FakeLLM` deterministic replies.
  - Hypothesis: random `expected` / `actual` strings into `ExactMatchScorer` always yield `score ∈ {0.0, 1.0}`.
- **Integration.** `tests/evals/test_runner_integration.py` — full orchestrator wired via `build_orchestrator(Settings())` (no real LLM call — uses `FakeLLM` via `Registry.scoped`). Gated on `RUN_EVALS=1` to keep CI fast.
- **Parity / E2E.** Optional `tests/evals/test_runner_lmstudio.py` against a real LM Studio (gated on `RUN_LMSTUDIO=1 RUN_EVALS=1`) — proves the harness works against a real LLM stack.

### Backwards-compatibility checklist.
- New `evals` package; no existing modules touched.
- New CLI subcommand; existing `mangomas chat` unchanged.
- `Settings.evals` defaults preserve current behaviour (no eval runs on startup).
- Deprecation notice: none.

### Acceptance criteria.
- `mangomas evals run --dataset tests/evals/fixtures/sample.jsonl --scorer exact_match --agent chat` produces a JSON report to stdout.
- All `tests/evals/test_*.py` pass without any RUN_* flag (unit tests run by default; integration gated).
- `scripts/check_coverage.py` reports `>= 90 %` on `src/mangomas/evals/*.py`.
- `isinstance(ExactMatchScorer(), Scorer)` is `True`.
- mypy --strict passes.

### Risk + mitigations.
- **Risk:** Dataset format choice locks early users in. **Mitigation:** ship JSONL only; add Parquet via a separate scorer-style extension once a real user requests it.
- **Risk:** `LLMAsJudgeScorer` produces inconsistent scores. **Mitigation:** rubric prompt is a settings-driven constant; users can tune; the harness records the judge's raw response in `ScoreResult.detail`.
- **Risk:** Eval runs balloon SQLite/Postgres turn tables. **Mitigation:** runner uses a `MemoryTurnRepository` (the existing `FakeRepository` repurposed) by default; persistence opt-in via `--persist`.

---

## Gap analysis summary — files touched by milestone

| File | M1 | M2 | M3 | M4 | M5 | M6 | M7 |
|---|---|---|---|---|---|---|---|
| `CHANGELOG.md` | ✓ rename | ✓ entry | ✓ entry | ✓ entry | ✓ entry | ✓ entry | ✓ entry |
| `pyproject.toml` | ✓ version | ✓ `[gcp]` extra | ✓ `[gcp]` extra, marker | ✓ `[gcp]` extra | ✓ `[postgres]` extra, marker | — | ✓ marker |
| `src/mangomas/api/app.py` | ✓ version | ✓ pass telemetry settings | — | — | ✓ async-aware repo close | — | — |
| `src/mangomas/config.py` | — | ✓ `TelemetrySettings` | ✓ Vertex fields on `LLMSettings` | ✓ `gcp_project_id` on `SecretsSettings` | ✓ pool/timeout fields on `DBSettings` | — | ✓ `EvalSettings` |
| `src/mangomas/telemetry.py` | — | ✓ exporter factory | — | — | — | — | — |
| `src/mangomas/errors.py` | — | ✓ `TelemetryConfigError` | — | — | — | — | — |
| `src/mangomas/composition.py` | — | — | ✓ vertex factory + registry | ✓ debug log | ✓ postgres factory + registry | — | — |
| `src/mangomas/adapters/llm/__init__.py` | — | — | ✓ conditional re-export | — | — | — | — |
| `src/mangomas/adapters/llm/vertex.py` | — | — | ✓ NEW | — | — | — | — |
| `src/mangomas/secrets/registry.py` | — | — | — | ✓ register gcp | — | — | — |
| `src/mangomas/secrets/gcp.py` | — | — | — | ✓ NEW | — | — | — |
| `src/mangomas/secrets/__init__.py` | — | — | — | ✓ conditional re-export | — | — | — |
| `src/mangomas/adapters/storage/postgres.py` | — | — | — | — | ✓ NEW | — | — |
| `src/mangomas/adapters/storage/__init__.py` | — | — | — | — | ✓ conditional re-export | — | — |
| `src/mangomas/adapters/storage/base.py` | — | — | — | — | ✓ docstring | — | — |
| `src/mangomas/cli/main.py` | — | — | — | — | — | — | ✓ `evals` subcommand |
| `src/mangomas/evals/*` | — | — | — | — | — | — | ✓ NEW package |
| `scripts/check_coverage.py` | — | — | (auto) | (auto) | (auto) | — | ✓ evals floor |
| `tests/conftest.py` | — | — | ✓ RUN_VERTEX gate | — | ✓ RUN_POSTGRES gate | — | ✓ RUN_EVALS gate |
| `tests/constants.py` | — | — | ✓ vertex env vars | — | ✓ postgres DSN | — | — |
| `tests/fakes.py` | — | — | — | — | — | — | — *(reuse existing)* |
| `tests/lmstudio/` | — | — | — | — | — | — | — |
| `tests/vertex/` | — | — | ✓ NEW | — | — | — | — |
| `tests/postgres/` | — | — | — | — | ✓ NEW | — | — |
| `tests/evals/` | — | — | — | — | — | — | ✓ NEW |
| `.github/workflows/ci.yml` | — | — | — | — | — | — *(optional: yamllint/actionlint)* | — |
| `.github/workflows/deploy.yml` | — | — | — | — | — | ✓ NEW | — |
| `deploy/` | — | — | — | — | — | ✓ NEW | — |
| `Dockerfile` | — | — | — | — | — | ✓ MANGOMAS_EXTRAS arg | — |
| `docker-compose.yml` | — | — | — | — | ✓ optional postgres service | — | — |
| `docs/architecture/observability.md` | — | ✓ Cloud Trace section | — | — | — | — | — |
| `docs/architecture/c3-component.md` | — | — | ✓ Vertex node | ✓ GCP secrets node | ✓ Postgres node | — | ✓ evals boundary |
| `docs/adr/0001-cloud-targets.md` | — | ✓ status notes | ✓ status notes | ✓ status notes | ✓ status notes | ✓ link | — |
| `docs/testing/vertex-e2e.md` | — | — | ✓ NEW | — | — | — | — |
| `docs/testing/postgres-e2e.md` | — | — | — | — | ✓ NEW | — | — |
| `docs/evals.md` | — | — | — | — | — | — | ✓ NEW |
| `deploy/README.md` | — | — | — | — | — | ✓ NEW | — |
| `README.md` | ✓ version | — | — | — | — | ✓ deploy link | — |

**Contention points** (one file edited by ≥ 2 milestones):
- `pyproject.toml` — five milestones edit the `[project.optional-dependencies]` table and `[tool.pytest.ini_options].markers` list. Merging order: M2 (creates `[gcp]` extra) → M3, M4 (extend the same extra). M5 introduces a separate `[postgres]` extra, so no conflict. M7 adds only a marker.
- `composition.py` — M3 (vertex factory) and M5 (postgres factory) both add a `try/except ImportError` block. M4 only adds a debug log. Merge M3 before M5 to keep diffs small.
- `config.py` — M2 (TelemetrySettings), M3 (LLMSettings extension), M4 (SecretsSettings extension), M5 (DBSettings extension), M7 (EvalSettings) all attach new sub-settings to `Settings`. Merge in milestone order; each one adds a new field — no overlap.
- `docs/architecture/c3-component.md` — four milestones add nodes. Merge order doesn't matter; each milestone owns its node.
- `tests/conftest.py` — M3, M5, M7 all add `RUN_*` gates. Pattern is line-for-line repetitive; conflicts are trivial to resolve.

---

## Dead-code / redundancy audit

- **Already covered by existing utilities — do NOT rewrite:**
  - Streaming buffered fallback: `mangomas.agents._streaming.stream_with_buffered_fallback` (verified at `_streaming.py:35`). Vertex (M3) and any new streaming agent must call this helper, not duplicate the `isinstance(ctx.llm, StreamingLLMClient)` check.
  - Test-scoped provider substitution: `Registry.scoped()` (`registry.py:73`). Parity-test suites for Vertex (M3) and Postgres (M5) must use this, not module-level monkey-patching.
  - Secret resolution: `composition._resolve_llm_secrets` (`composition.py:54`). M4's GCP Secret Manager provider must NOT add a parallel resolution path; it plugs into the same seam.
  - Error → HTTP status mapping: `api/app.py::_ERROR_STATUS` (`app.py:45`). New errors that cross the API boundary must extend `MangomasError` and pick up the closest MRO entry; no new mapping needed if the inheritance is right.
  - Sync-to-async I/O wrapper: `asyncio.to_thread`. asyncpg already async, so M5 doesn't need it for query execution; only `pool.close()` may need scheduling care (see M5 file-level plan).
  - JSON log envelope: `JsonFormatter` (`telemetry.py:56`). M2's Cloud Trace exporter does NOT need to change the log format — Cloud Logging consumes the existing envelope.
  - Correlation propagation: `mangomas.correlation` + `AccessLogMiddleware`. M2's Cloud Trace spans, M3's Vertex calls, M5's Postgres queries automatically inherit `correlation_id` via OTel baggage — no per-adapter wiring needed.

- **Becomes obsolete after these milestones land:**
  - `ConsoleSpanExporter` remains the default (per ADR-001 row 4) — not obsolete.
  - No code becomes obsolete; each milestone is additive.

- **Currently unused, should be cleaned up:**
  - I did not find dead code in the v0.2.0 baseline during this read. The codebase is unusually tidy. If anything is uncovered during M3+ implementation (e.g. a now-redundant `hasattr` check at `app.py:85` once the Postgres lifespan close lands), flag it in that milestone's PR.

- **Tempting to write but don't:**
  - "A `CloudLogHandler`" — not needed. The existing `JsonFormatter` + Cloud Run's automatic log ingestion handles this. Cloud Logging picks up stdout JSON automatically.
  - "An `AsyncSecretsProvider` protocol" — not needed. Sync `get()` is called once per startup, blocking is fine.
  - "A `MigrationsRunner`" for Postgres — defer; idempotent `CREATE TABLE IF NOT EXISTS` is sufficient for v0.4.0 and matches SQLite's behaviour.

---

## Sequential thinking — execution order

```
M1 — Cut v0.2.0 ───────────┐
                           ▼
                       M2 — Cloud Trace exporter
                           │
                  ┌────────┴────────┐
                  ▼                 ▼
              M3 — Vertex       M4 — GCP Secret Manager
                  │                 │
                  └────────┬────────┘
                           ▼
                       M5 — Postgres
                           │
                           ▼
                       M6 — Cloud Run pipeline

  M7 — Eval harness ───────────────────►  (runs in parallel from after M1)
```

**Gates and rationale:**

1. **Start M2 once M1 is met.** M1 done = `git tag v0.2.0` exists and CHANGELOG `[Unreleased]` block is empty. M2 needs an empty `[Unreleased]` to write into.
2. **Start M3 and M4 once M2 is met.** M2 done = `MANGOMAS_TELEMETRY__EXPORTER=gcp` works in a smoke test, and the `[gcp]` optional extra exists in `pyproject.toml`. M3 and M4 both extend the same `[gcp]` extra — landing M2 first avoids three milestones racing on the same dep table.
3. **Merge M4 before M3.** Smaller surface (one new file, one factory). Frees M3's PR review from the secrets-resolution distraction.
4. **Start M5 once M2 is met.** M5 doesn't strictly depend on M3/M4 — it needs only M2 for span propagation through DB calls. Can land in parallel with M3.
5. **Start M6 once M2, M3, M4, M5 are met.** The env-var contract in `deploy/README.md` must cite every `MANGOMAS_*` var the cloud build needs. Landing M6 before M5 would mean a second pass on `deploy/README.md` once Postgres lands.
6. **M7 starts after M1.** It does not touch any cloud-target code. The only cross-cutting concern is that `LLMAsJudgeScorer` benefits from M3 (Vertex) being available — but it works against `FakeLLM` in tests and `LMStudioClient` in default deployment, so it does not block.

**Parallel-merge order to avoid conflict:**

| Order | Milestone | Rationale |
|---|---|---|
| 1 | M1 | Baseline tag. |
| 2 | M2 | Establishes `[gcp]` extra. |
| 3 | M4 | Smallest cloud-adapter; sets the registration pattern. |
| 4 | M3 | Reuses pattern from M4. |
| 5 | M5 | Independent extra; touches `app.py` lifespan (the only contention with M3 is `composition.py`, trivial). |
| 6 | M7 | Independent package; can merge any time after M1, but landing here lets the eval harness target Vertex if developers want. |
| 7 | M6 | Aggregates everything. |

---

## Observability & debugging plan

**Single-request trace through the v0.4.0 stack:**

1. Client sends `POST /agents/chat/invoke` with `X-Request-ID: abc12345`.
2. `AccessLogMiddleware` (existing) sanitises the inbound header → sets `correlation_id` ContextVar → pushes to OTel baggage as `mangomas.correlation_id` → starts latency timer.
3. `TraceMiddleware` (existing) opens an OpenTelemetry span. With M2's Cloud Trace exporter active, the span is batched to Cloud Trace. The trace id is W3C-formatted and visible in `traceparent` outbound headers.
4. Route handler invokes `Orchestrator.dispatch("chat", request)`.
5. `ChatAgent.handle` calls `ctx.llm.complete(messages)`.
   - **Vertex path (M3):** `VertexLLMClient.complete` calls `aiplatform.GenerativeModel(...).generate_content_async(...)`. The Vertex SDK reads OTel context from the current span and attaches trace headers to the gRPC call (free with `opentelemetry-instrumentation-grpc` if installed; otherwise the trace remains in-process). Adapter logs at `INFO`: `"Vertex client initialized"` (once), per-request at `ERROR` on failure with `correlation_id` field (injected by `CorrelationFilter`).
   - **LM Studio path (existing):** Same flow, logs at `ERROR` per `lmstudio.py:77`.
6. `Orchestrator.dispatch` calls `repo.save_turn(...)`.
   - **Postgres path (M5):** `PostgresRepository.save_turn` runs `INSERT INTO turns ...`. asyncpg does not auto-instrument with OTel, but the call happens inside the request span — the latency is captured by the parent. Logs at `ERROR` on failure with `correlation_id`.
   - **SQLite path (existing):** Same flow.
7. Response flows back. `AccessLogMiddleware` emits the INFO access log with `extra={"request_id": "abc12345", "correlation_id": "abc12345", "trace_id": "0af7...", "span_id": "...", ...}` — verified against `observability.md:67-81`.
8. Cloud Logging ingests the stdout JSON via Cloud Run's automatic log sink — the JSON envelope is unchanged from v0.2.0.

**New fields added to the JSON log envelope:**

Per-milestone, fields surface only on relevant log records (not every line):

| Field | Emitted by | Milestones |
|---|---|---|
| `exporter` | `telemetry._build_span_exporter` | M2 |
| `gcp_project` | `VertexLLMClient.__init__`, `GcpSecretManagerProvider.__init__` | M3, M4 |
| `gcp_location` | `VertexLLMClient.__init__` | M3 |
| `model` (refined) | `VertexLLMClient` (was just `base_url` before) | M3 |
| `pool_min`, `pool_max`, `dsn_host` | `PostgresRepository.__init__` | M5 |
| `eval_case_id`, `pass_rate`, `p50_ms`, `p95_ms` | `EvalRunner` | M7 |

The required-fields contract from `observability.md:67-81` (`timestamp`, `severity`, `logger`, `message`, `request_id`, `correlation_id`, `trace_id`, `span_id`, `method`, `path`, `status_code`, `latency_ms`) stays unchanged — new fields are additive and confined to the records that produce them.

**Correlation id sanity check:** The same `correlation_id` value will appear on:
- The HTTP `X-Request-ID` response header.
- Every log record emitted under the request span (via `CorrelationFilter`).
- The OTel baggage key `mangomas.correlation_id` on Vertex / Postgres / Secret Manager calls (verified: those SDKs pick up ambient OTel context).
- The Cloud Trace span attributes once M2 routes traces through Cloud Trace.

---

## Test suite shape after all milestones land

| Category | Files | Gate | Runs in CI by default | Coverage contribution |
|---|---|---|---|---|
| Unit (`tests/test_*.py`) | ~30 today + ~6 new (test_vertex, test_postgres, test_secrets_gcp, test_evals_*, test_runner) | none | yes | counts toward 90 % global floor |
| Integration (`tests/integration/`) | 1 today + 0 new (M5's parity is in `tests/postgres/`) | `RUN_INTEGRATION=1` | no | excluded from coverage gate |
| LM Studio (`tests/lmstudio/`) | 6 today + 0 new | `RUN_LMSTUDIO=1` | no | excluded |
| Vertex (`tests/vertex/`) | 0 today + 6 new | `RUN_VERTEX=1` | no | excluded |
| Postgres (`tests/postgres/`) | 0 today + 3 new | `RUN_POSTGRES=1` | no | excluded |
| Evals (`tests/evals/`) | 0 today + 5 new | unit always runs; `RUN_EVALS=1` for integration runner | partial | unit runs count toward floor |

**Global floor:** 90 % stays unchanged.

**New per-package floors** added to `scripts/check_coverage.py`:
- `Floor("src/mangomas/evals/*.py", 90, "evals")` — M7.

**No new floor needed** for `vertex.py`, `postgres.py`, `secrets/gcp.py` — they fall under existing floors:
- `Floor("src/mangomas/adapters/**/*.py", 85, "adapters")` covers Vertex and Postgres.
- `Floor("src/mangomas/secrets/*.py", 100, "secrets")` covers the GCP secrets provider.

**CI matrix unchanged** — `python-version: ["3.11", "3.12"]`, lint job runs `ruff` + `mypy --strict` over `src tests scripts`.

**New CI considerations:**
- `pytest --cov` is run by default without the optional `[gcp]` / `[postgres]` extras. Cloud-adapter code paths are exercised by unit tests via SDK monkeypatching, so coverage of those modules remains measurable without the heavy deps.
- A separate optional CI job could be added later to install `[gcp,postgres]` and run the parity suites against ephemeral GCP / Postgres instances — out of scope for this plan per ADR-001 "no cloud resources created by this branch".

---

## Open Questions

1. **Vertex region default.** Should `MANGOMAS_LLM__GCP_LOCATION` carry no default (forcing operators to set it explicitly) or default to a region like `us-central1`? Recommendation: **no default** — Vertex models have region-specific availability and a wrong default silently routes traffic. Decision needed before M3 implementation starts.

2. **Postgres driver.** asyncpg (faster, fewer features) vs psycopg3 with `async` mode (more standard, supports connection lifecycle hooks)? Recommendation: **asyncpg** — its API surface is smaller and the `PostgresRepository` wrapper insulates the choice. Decision needed before M5 implementation starts.

3. **Evaluation dataset format.** JSONL (simple, no extra dep) vs Parquet (columnar, requires pyarrow)? Recommendation: **JSONL for v0.5.0**; add Parquet later as an optional extra if a real user requests it. Decision needed before M7 implementation starts.

4. **`SecretsSettings.gcp_project_id` vs `LLMSettings.gcp_project_id`.** Both M3 and M4 propose a `gcp_project_id` field. Should they default to the same value (a top-level `Settings.gcp_project_id`)? Recommendation: keep them independent — a user might run Vertex in one project and Secret Manager in another. Decision needed before M3 / M4 implementation starts.

5. **`Scorer` protocol async-ness.** Should `score()` be sync (matches `SecretsProvider`) or async (matches `Agent.handle`)? Recommendation: **async** — `LLMAsJudgeScorer` calls an LLM, which is async; embedding scorers may call an API. Decision needed before M7 implementation starts.

6. **Cloud Run deployment trigger.** On every release published, or only on tags matching `v[0-9]+\.[0-9]+\.[0-9]+`? Recommendation: tag pattern match — pre-release tags (e.g. `v0.4.0-rc1`) should not deploy production. Decision needed before M6 implementation starts.

7. **`TurnRepository.close()` sync-vs-async resolution.** Should the protocol be modified to mark `close` as `Awaitable[None] | None` to remove the `inspect.iscoroutinefunction` branch in `app.py`? Recommendation: **no** — the existing protocol is fine; the consumer-side branch is a 2-line change. Modifying the protocol risks third-party `TurnRepository` implementations.
