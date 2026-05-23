# Next Steps & Roadmap

This document tracks planned improvements to Mango-Mas V2.
Near-term items are sequenced; mid- and long-term items are ordered by
dependency, not priority.  All items follow the same design rules as the
existing codebase: env-driven configuration, registry/protocol-based
extension, backwards-compatible contracts.

---

## Near term

_(The Vertex AI LLM provider, Postgres `TurnRepository`, GCP Secret
Manager backend, and the offline evaluation harness all landed in
v0.3.0 — see "Done in v0.3.0" below. Next near-term items are the
Cloud Logging / Cloud Trace exporter swap and the Cloud Run deployment
pipeline, promoted from "Mid term".)_

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
contract by default; gives operators an opt-in "fail loud" mode for
production.

---

## Done in v0.3.0

### Vertex AI LLM provider

`VertexClient` in `src/mangomas/adapters/llm/vertex.py` satisfies
`LLMClient`, `PingableLLMClient`, and `StreamingLLMClient` via
`vertexai.generative_models`. Registered through the existing
`llm_registry`; activate with `MANGOMAS_LLM__PROVIDER=vertex` and the
`vertex` optional extra (`pip install 'mangomas[vertex]'`). New
`LLMSettings` fields: `project_id`, `location`, `credentials_path`. The
existing `secret_ref` flow is reused — the resolved value becomes the
service-account JSON body. See `docs/adapters/vertex.md` and
`docs/testing/vertex-e2e.md`.

### Cloud SQL / Postgres storage provider

`PostgresRepository` (`src/mangomas/adapters/storage/postgres.py`)
satisfies `TurnRepository` and the new `AsyncCloseableRepository`
extension protocol. Backed by `asyncpg` with a connection pool — no
`threading.Lock` (native async). Registered as
`_storage_registry.register("postgres", _postgres_factory)`. Activate
via `MANGOMAS_DB__PROVIDER=postgres` plus
`MANGOMAS_DB__URL=postgresql://...` and the `postgres` optional extra
(`pip install 'mangomas[postgres]'`). A JSONB codec is registered on
every connection so `list_turns` returns dicts (matching SQLite's
row shape). testcontainers-backed integration suite under
`tests/postgres/` gated by `RUN_POSTGRES=1`. See
`docs/testing/postgres-integration.md`.

### Cloud Secret Manager provider

`GCPSecretManagerProvider` (`src/mangomas/secrets/gcp.py`) satisfies
`SecretsProvider`. Lazily registered in `secrets_registry` at
orchestrator-build time. Activate via `MANGOMAS_SECRETS__PROVIDER=gcp`
plus `MANGOMAS_SECRETS__PROJECT_ID=...` and the `gcp` optional extra
(`pip install 'mangomas[gcp]'`). Collapses all failure modes into
`None` per [ADR-002](docs/adr/0002-secrets-provider-error-semantics.md);
operators MUST alert on `logger=mangomas.secrets.gcp severity=ERROR`.

### Evaluation harness

`src/mangomas/eval/` ships the `Scorer` protocol, `scorer_registry`, a
JSONL dataset loader, `EvalRunner` (reuses the existing
`Orchestrator`), `EvalReport`, three built-in scorers (`exact_match`,
`llm_judge`, `embedding`), an `EvalSettings` block (`MANGOMAS_EVAL__*`),
and a `mangomas eval` CLI subcommand. The embedding scorer raises
`NotImplementedError` against providers that don't expose `.embed()`
(see "Embedding-capable LLM provider" under "Long term"). See
`docs/eval/harness.md`.

### Concurrency regression tests

`tests/test_sqlite_concurrency.py` closes the gap from v0.1.0's
`threading.Lock` fix with a direct 50-way `asyncio.gather` regression.
`tests/postgres/test_concurrency.py` pins the asyncpg pool's
concurrent-write contract symmetrically.

### Coverage / hygiene tightening

Per-package floors in `scripts/check_coverage.py` raised to match the
post-v0.3.0 actuals: `composition` / `api` / `cli` / `global` all
90 → 95. New `eval` floor at 95 %. `pyproject.toml`
`--cov-fail-under=90` → `95`. The backwards-compat
`mangomas.api.correlation` re-export shim was removed; imports must use
the canonical `mangomas.correlation` path. The `DEFAULT_ERROR_DETAIL_TRUNCATE`
constant replaces inline `[:200]` literals across the cloud adapters.

See [`docs/architecture/cloud-providers.md`](docs/architecture/cloud-providers.md)
for the full configuration matrix and the lazy-SDK-import /
ambient-identity pattern shared across all three cloud adapters.

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
`LLMSettings.secret_ref` is resolved at orchestrator-build time. The
GCP Secret Manager backend landed in v0.3.0.

### Per-request correlation IDs

`src/mangomas/correlation.py` exposes a `ContextVar` + `CorrelationFilter`;
`AccessLogMiddleware` reads inbound `X-Request-ID`, sets the ContextVar,
pushes the value into OpenTelemetry baggage as `mangomas.correlation_id`,
and echoes it on the outgoing response.

---

## Long term

_(The first long-term capability — the evaluation harness — landed in
v0.3.0; see "Done in v0.3.0" above. Follow-ups below.)_

### Embedding-capable LLM provider

The evaluation harness ships an `EmbeddingScorer` that requires an
`LLMClient` exposing `.embed()`. None of today's providers do. Add an
embedding surface to either the Vertex adapter (`text-embedding-004` via
`TextEmbeddingModel.get_embeddings_async`) or a new dedicated provider.
Once the surface is present, the scorer becomes operational with no
harness changes.

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
