# Next Steps & Roadmap

This document tracks planned improvements to Mango-Mas V2.
Near-term items are sequenced; mid- and long-term items are ordered by
dependency, not priority.  All items follow the same design rules as the
existing codebase: env-driven configuration, registry/protocol-based
extension, backwards-compatible contracts.

---

## Near term

_(The three GCP-target items from "Mid term" landed in v0.3.0 — see
"Done in v0.3.0" below. Next near-term items are the Cloud Logging /
Cloud Trace exporter swap and the Cloud Run deployment pipeline,
promoted from "Mid term".)_

### Cloud Logging + Cloud Trace exporter swap

Add a Cloud Trace OTLP exporter behind the existing
`configure_telemetry()` entry point. Activate via a new
`MANGOMAS_TELEMETRY__EXPORTER=gcp` option. `MANGOMAS_LOG__FORMAT=json`
is already supported.

### Cloud Run deployment pipeline

Add a `deploy/` directory with:
- Cloud Run service YAML (or Terraform module).
- GitHub Actions workflow step for image push to Artifact Registry.
- Environment-variable contract documented for Cloud Run service configuration.

### SecretsSettings.strict mode (ADR-002 follow-up)

Add `SecretsSettings.strict: bool = False`; when set, cloud secrets
backends raise a new `SecretsResolutionError` instead of returning
`None` on auth/permission/timeout failures. Preserves the local-dev
contract by default; gives operators an opt-in "fail loud" mode.

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

## Done in v0.3.0 (GCP swap-in — see ADR-001)

The three remaining GCP swap-matrix entries from ADR-001 shipped
together. Each is selectable via Settings; no core changes were
required.

### Vertex AI LLM provider

- [x] `VertexLLMClient` (`src/mangomas/adapters/llm/vertex.py`)
      satisfies `LLMClient`, `StreamingLLMClient`, `PingableLLMClient`.
      Registered as `llm_registry.register("vertex", _vertex_factory)`.
      Activate via `MANGOMAS_LLM__PROVIDER=vertex` +
      `MANGOMAS_LLM__PROJECT=...`. Identity via ADC / Workload Identity
      Federation. 6-scenario E2E suite under `tests/vertex/` mirrors
      `tests/lmstudio/` exactly. See `docs/testing/vertex-e2e.md`.

### Cloud SQL / Postgres storage provider

- [x] `PostgresRepository`
      (`src/mangomas/adapters/storage/postgres.py`) satisfies
      `TurnRepository` and the new `AsyncCloseableRepository` extension
      protocol. Backed by `asyncpg` with a connection pool — no
      `threading.Lock`. Registered as
      `_storage_registry.register("postgres", _postgres_factory)`.
      Activate via `MANGOMAS_DB__PROVIDER=postgres` +
      `MANGOMAS_DB__URL=postgresql://...`. testcontainers-backed
      integration suite under `tests/postgres/` gated by
      `RUN_POSTGRES=1`. See `docs/testing/postgres-integration.md`.

### Cloud Secret Manager provider

- [x] `GCPSecretManagerProvider` (`src/mangomas/secrets/gcp.py`)
      satisfies `SecretsProvider`. Lazily registered in
      `secrets_registry` at orchestrator-build time. Activate via
      `MANGOMAS_SECRETS__PROVIDER=gcp` +
      `MANGOMAS_SECRETS__PROJECT_ID=...`. Collapses all failure modes
      into `None` per [ADR-002](docs/adr/0002-secrets-provider-error-semantics.md);
      operators MUST alert on
      `logger=mangomas.secrets.gcp severity=ERROR`.

See [`docs/architecture/cloud-providers.md`](docs/architecture/cloud-providers.md)
for the full configuration matrix and the lazy-import / ambient-identity
pattern shared across all three.

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
