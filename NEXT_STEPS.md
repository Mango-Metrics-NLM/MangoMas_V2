# Next Steps & Roadmap

This document tracks planned improvements to Mango-Mas V2.
Near-term items are sequenced; mid- and long-term items are ordered by
dependency, not priority.  All items follow the same design rules as the
existing codebase: env-driven configuration, registry/protocol-based
extension, backwards-compatible contracts.

---

## Near term

### LM Studio end-to-end scenarios (follow-up to v0.1.0 ping scaffold)

The `tests/lmstudio/` directory currently holds a single readiness-probe
smoke test.  The remaining five scenarios from the
[LM Studio E2E Scenario Plan](docs/testing/lmstudio-e2e.md) are the
immediate next implementation step — in order:

- [ ] **Chat happy path** — `POST /agents/chat/invoke` against a real LM
  Studio instance; assert 200, non-empty `content`, and persisted turn.
- [ ] **Streaming happy path** — `POST /agents/chat/stream` SSE; assert at
  least one `event: token` frame and the `event: done` sentinel.
- [ ] **Streaming fallback warning** — exercise the buffered-completion
  fallback path when the LLM does not implement `StreamingLLMClient`; assert
  warning log line and complete response.
- [ ] **Summarize agent** — invoke `summarize` through the public API with a
  multi-message thread; assert persisted turn with `agent=summarize`.
- [ ] **Error path — unavailable model** — point `LMSTUDIO_MODEL` at an
  unknown id; assert `502` error envelope from `LMStudioError`/`LLMBadResponse`
  and a structured warning log entry.

All tests must be gated by `RUN_LMSTUDIO=1`, read model id from
`LMSTUDIO_MODEL`, and use `DEFAULT_LLM_BASE_URL` / `DEFAULT_LLM_MODEL` as
fallback defaults.  No hardcoded model ids.

### Planner / Reviewer streaming support

`PlannerAgent` and `ReviewerAgent` implement `Agent` but not `StreamingAgent`.
Add `_do_stream` to both so the streaming endpoint can deliver plan/review
tokens incrementally without the buffered fallback.

### Secrets-manager seam

Add a `SecretsProvider` abstraction (from ADR-001) consumed at settings
construction time so that credentials can be read from environment variables,
files, or a secrets manager without changing agent/adapter code.

### Per-request correlation IDs

Propagate a request-scoped correlation ID through log records and the OTel
span context so that distributed traces can be correlated across services.

---

## Mid term (GCP swap-in — see ADR-001)

These items implement the cloud-target swap matrix from
[ADR-001](docs/adr/0001-cloud-targets.md).  Each boundary is swapped
independently through the existing registry mechanism; no core changes.

### Vertex AI LLM provider

Implement `VertexLLMClient` satisfying `LLMClient` + `StreamingLLMClient`.
Register as `_llm_registry.register("vertex", ...)`.
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
