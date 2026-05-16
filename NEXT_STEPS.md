# Next Steps & Roadmap

This document tracks planned improvements to Mango-Mas V2.
Near-term items are sequenced; mid- and long-term items are ordered by
dependency, not priority.  All items follow the same design rules as the
existing codebase: env-driven configuration, registry/protocol-based
extension, backwards-compatible contracts.

---

## Near term

_(All near-term workstreams from v0.1.0 landed in v0.2.0 — see "Done in v0.2.0"
below. Next near-term item is the Vertex AI provider — see "Mid term".)_

---

## Done in v0.2.0

### LM Studio end-to-end scenarios (scenarios 2–6)

All five follow-up scenarios from the
[LM Studio E2E Scenario Plan](docs/testing/lmstudio-e2e.md) ship under
`tests/lmstudio/`, gated on `RUN_LMSTUDIO=1`:

- [x] **Chat happy path** — `tests/lmstudio/test_chat_invoke.py`
- [x] **Streaming happy path** — `tests/lmstudio/test_chat_stream.py`
- [x] **Streaming fallback warning** — `tests/lmstudio/test_stream_fallback.py`
      (uses `Registry.scoped()` to swap in a `NonStreamingLMStudioClient`)
- [x] **Summarize agent** — `tests/lmstudio/test_summarize_invoke.py`
- [x] **Error path — unavailable model** — `tests/lmstudio/test_unknown_model.py`

Shared fixtures (`lmstudio_base_url`, `lmstudio_model`,
`lmstudio_orchestrator`, `lmstudio_app`) live in `tests/lmstudio/conftest.py`.

### Planner / Reviewer streaming support

Both `PlannerAgent` and `ReviewerAgent` now satisfy `StreamingAgent` via an
async `stream()` method that yields LLM tokens incrementally and falls back
to a single buffered chunk + warning log when the configured client does
not implement `StreamingLLMClient`.

### Secrets-manager seam

`src/mangomas/secrets/` ships the `SecretsProvider` protocol,
`EnvSecretsProvider` env-var backend, and `secrets_registry`.
`LLMSettings.secret_ref` is resolved at orchestrator-build time. Cloud
backends (GCP Secret Manager) still tracked under "Mid term".

### Per-request correlation IDs

`src/mangomas/api/correlation.py` exposes a `ContextVar` + `CorrelationFilter`;
`AccessLogMiddleware` reads inbound `X-Request-ID`, sets the ContextVar,
pushes the value into OpenTelemetry baggage as `mangomas.correlation_id`,
and echoes it on the outgoing response.

---

## Mid term (GCP swap-in — see ADR-001)

These items implement the cloud-target swap matrix from
[ADR-001](docs/adr/0001-cloud-targets.md).  Each boundary is swapped
independently through the existing registry mechanism; no core changes.

### Vertex AI LLM provider

Implement `VertexLLMClient` satisfying `LLMClient` + `StreamingLLMClient`.
Register as `llm_registry.register("vertex", ...)`.
Activate via `MANGOMAS_LLM__PROVIDER=vertex`.

### Cloud SQL / Postgres storage provider

Implement `PostgresRepository` satisfying `TurnRepository`.
Register as `_storage_registry.register("postgres", ...)`.
Activate via `MANGOMAS_DB__PROVIDER=postgres`.

### Cloud Secret Manager provider

Implement the `SecretsProvider` abstraction above backed by Google Secret
Manager.  Add `MANGOMAS_SECRETS__PROVIDER=gcp` activation path.

### Cloud Logging + Cloud Trace exporter swap

Add a structured JSON log formatter and a Cloud Trace OTLP exporter behind the
existing `configure_telemetry()` entry point.  Swap via
`MANGOMAS_LOG__FORMAT=json` (already supported) and a new
`MANGOMAS_TELEMETRY__EXPORTER=gcp` option.

### Cloud Run deployment pipeline

Add a `deploy/` directory with:
- Cloud Run service YAML (or Terraform module).
- GitHub Actions workflow step for image push to Artifact Registry.
- Environment-variable contract documented for Cloud Run service configuration.

---

## Long term

### Evaluation harness

Offline evaluation of agent responses against a dataset of expected
input/output pairs.  Pluggable scorer (exact match, LLM-as-judge, embedding
similarity) behind a `Scorer` protocol.

### Multi-agent workflows

Composition of multiple agents (e.g. planner → executor → reviewer) through a
declarative graph definition consumed by `Orchestrator`.  Must remain backwards
compatible: existing single-agent dispatch is unchanged.

### Multi-tenancy

Tenant-scoped conversation storage and agent configuration (per-tenant
`AgentSettings` registry) without leaking state across tenants.

### Agent marketplace / dynamic loading

`agent_registry` loading from external packages via entry-point discovery
(`importlib.metadata.entry_points`) so that third-party agents can be
installed and registered without modifying `composition.py`.

---

## Deferred / out of scope for this branch

- GCP resource provisioning scripts (no cloud resources created by this repo).
- Model fine-tuning or RLHF pipelines.
- UI / chat interface (out of scope; existing `cli/` covers local interaction).
