# Next Steps & Roadmap

This document tracks planned improvements to Mango-Mas V2.
Near-term items are sequenced; mid- and long-term items are ordered by
dependency, not priority.  All items follow the same design rules as the
existing codebase: env-driven configuration, registry/protocol-based
extension, backwards-compatible contracts.

---

## Near term

_(All cloud adapters (Vertex AI, Postgres, GCP Secret Manager) and the
offline evaluation harness landed in v0.3.0. The Claude Code enterprise
harness landed in Unreleased. v0.3.1 cherry-picked the 7-milestone GCP
swap-in plan from PR #6 (rejecting the destructive code rollback),
fixed lint/type issues, and raised test coverage to 98.16 % / 515 tests.
See `docs/plans/20260523T133844Z-gcp-swapin-and-evals-plan.md` for the
full plan. Next near-term items:)_

### Harness metrics-exporter selection

Wire `HarnessSettings.metrics_namespace` into a configurable OTel
exporter so the `harness.agent_invoke` parent spans can be routed to a
different OTLP endpoint than the application spans. Today the
namespace is honoured by the tracer but exporter selection is
shared. Activate via a new `MANGOMAS_HARNESS__METRICS_EXPORTER` env;
default falls through to the application-wide exporter to preserve
the existing behaviour. Pairs naturally with the Cloud Trace work
below.

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

## Done on the harness branch (Unreleased)

### Claude Code enterprise harness

End-to-end Claude Code harness landed as a non-breaking opt-in layer:

- **8 skills** under `.github/skills/<name>/SKILL.md` covering
  testing, adapter authoring, agent addition, error taxonomy,
  observability, config, release, and topology.
- **12 sub-agents** under `.github/agents/<parent>/<slug>.agent.md`
  grouped under the 4 parent agents. The new `sub_agents:`
  frontmatter key is optional and backwards-compatible.
- **`HarnessSettings`** (env prefix `MANGOMAS_HARNESS__`,
  `enabled=False` default) drives whether `build_orchestrator`
  returns a `_HarnessOrchestrator` wrapper that adds a
  `harness.agent_invoke` parent span over `dispatch` *and*
  `stream_dispatch`. Pipeline + fan-out topologies inherit the wrap.
- **`scripts/lint_agent_frontmatter.py`** enforces frontmatter
  schemas (Pydantic), resolves `sub_agents:` slugs, and gates
  protected core paths via a `BREAKING-CHANGE` marker. Wired into
  CI as `frontmatter-lint`.
- **`scripts/harness_session_start.py`** emits a single-line JSON
  probe report on session start (venv + LM Studio reachability).
  Always returns `EXIT_OK` so a failed probe never blocks a session.
- **PR template** + **secret-scan job** + **ADR template** complete
  the PR automation.
- Composition coverage rises 90 % → 100 % via six new tests covering
  every harness branch (dispatch wrap, stream wrap, file-memory
  factory, memory-enabled wiring, linter EXIT_SCHEMA, git-failure
  branch in `_staged_diff`). Global coverage 96.95 % across 505
  unit tests.

See `.github/agents/`, `.github/skills/`, and the new `harness:`
block in `Settings`. C4 diagrams in `docs/architecture/` (c2 + c3)
describe where the harness sits.

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

## Done on the RAG branch (Unreleased)

### Retrieval-augmented generation

The full RAG port landed as a non-breaking opt-in layer
(`embeddings.enabled` / `vector.enabled` default `False`):

- **`EmbeddingClient` seam** (`adapters/embeddings/`) with three backends:
  `LMStudioEmbeddingClient`, `SentenceTransformersEmbeddingClient`
  (`embeddings-local` extra), and `VertexEmbeddingClient`
  (`text-embedding-004`, ADC-only, `vertex` extra). This closes the
  long-term "embedding-capable provider" gap — the `EmbeddingScorer` is
  now operational against a real provider via `ScorerContext.embeddings`.
- **`VectorStoreRepository` seam** (`adapters/vector/`) with
  `ChromaVectorStore` (`rag` extra). Cosine space + `1 - distance/2`
  similarity keeps scores in `[0, 1]`.
- **`rag/` package** — word-window chunker, document loader,
  `IngestionPipeline` (idempotent re-ingest via `delete_by_source`), and
  `Retriever` + `RetrievalTool` (auto-discovered by `ToolAgent`).
- **CLI** — `mangomas rag ingest` / `mangomas rag query`.
- **Shared adapter error helpers** (`adapters/_http_errors.py`,
  `adapters/_vertex_errors.py`) de-duplicate the httpx + Vertex error
  translation across the chat and embedding adapters.
- New `rag` 95 % coverage floor; 639 tests, 97.95 % global coverage.

See the `mango-rag` skill (`.github/skills/mango-rag/SKILL.md`) and the
C4 diagrams in `docs/architecture/`.

---

## Long term

_(The first long-term capability — the evaluation harness — landed in
v0.3.0; the embedding-capable provider that unblocked its `EmbeddingScorer`
landed on the RAG branch above. Follow-ups below.)_

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
