# Spec-0049: Google Cloud Platform (GCP) Local CLI Integration Demonstration

- **Status:** Draft
- **Linked ADR:** _none — no protocol, error type or registry boundary change_
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added — Google Cloud Platform (GCP) local CLI integration demonstration surface and runbook`

## Problem

Mango-Mas is architected as a local-first, modular multi-agent platform designed to run entirely on-device with LM Studio and SQLite, with protocol-level swap-in capabilities for enterprise cloud infrastructure on Google Cloud Platform (GCP). While production deployment to Cloud Run with Cloud SQL and Secret Manager is specified (Spec-0004, Spec-0035, Spec-0038), developers and operators lack a formal, end-to-end specification and execution runbook for demonstrating, evaluating, and operating the full suite of GCP services directly from their local CLI workstations without intermediate container builds or cloud deployments.

Furthermore, local small models (e.g., 7B/8B instruction-tuned models) frequently exhibit operational gaps during complex evaluation suites, such as missing `/v1/embeddings` endpoints, markdown fencing around structured JSON outputs, or tool-calling parsing failures (as documented in the `NEXT_STEPS.md` E2E model gap triage). Demonstrating full GCP parity from the local CLI validates that switching `MANGOMAS_LLM__PROVIDER=vertex`, `MANGOMAS_EMBEDDINGS__PROVIDER=vertex`, `MANGOMAS_DB__PROVIDER=postgres`, and `MANGOMAS_SECRETS__PROVIDER=gcp` resolves these operational gaps while maintaining 100% contract and protocol parity with local development workflows.

## Requirements

- **R1: Full GCP Integration & Service Parity Architecture**
  - The system must provide complete functional parity across five core Google Cloud services:
    - **Vertex AI LLM** (`src/mangomas/adapters/llm/vertex/`): Implements `LLMClient`, `StreamingLLMClient`, and `PingableLLMClient` protocols, delegating generation to Gemini foundation models (e.g., `gemini-2.5-flash`, `gemini-2.5-pro`) using ambient Application Default Credentials (ADC).
    - **Vertex AI Embeddings** (`src/mangomas/adapters/embeddings/vertex.py`): Implements `EmbeddingClient` protocol (`embed`, `embed_batch`, `aclose`), batching text embeddings to `text-embedding-004` (768 dimensions) via ADC.
    - **Cloud SQL / Postgres** (`src/mangomas/adapters/storage/postgres.py`): Implements `TurnRepository` protocol using `asyncpg` connection pooling over standard PostgreSQL DSN, with automatic idempotent schema migrations (`turns` table) and row-shape JSON parity with SQLite.
    - **GCP Secret Manager** (`src/mangomas/secrets/gcp.py`): Implements `SecretsProvider` protocol with fail-closed resolution (`MANGOMAS_SECRETS__STRICT=true` raising `SecretsResolutionError`) and ADC authentication.
    - **Cloud Telemetry** (`src/mangomas/telemetry/`): Integrates OpenTelemetry `CloudTraceSpanExporter` for distributed tracing and `JsonFormatter` for structured Cloud Logging with trace context propagation (`trace_id`, `span_id`).
- **R2: Local CLI Demonstration Surface**
  - The demonstration must execute entirely via the native `mangomas` CLI commands (`agents`, `chat`, `rag`, `history`, `eval`, `workflow`) without requiring local container orchestration or direct API gateway bypass:
    - `mangomas chat "<msg>" -a <agent>`: Executes interactive single-turn or multi-agent conversations targeting Vertex AI Gemini models.
    - `mangomas rag ingest <path>` / `mangomas rag query "<text>"`: Performs word-window document chunking, batch embedding generation via Vertex AI, local Chroma vector storage, and ranked cosine similarity retrieval.
    - `mangomas history --limit <n>`: Inspects recent conversation turns persisted to Cloud SQL Postgres.
    - `mangomas workflow run "<msg>" -f <spec>`: Executes declarative multi-agent workflows and evaluates sync acceptance predicates against Vertex AI outputs.
    - `mangomas eval -d <dataset> -s <scorer> --gate`: Runs automated evaluations with quality gates and regression diffing against Vertex AI.
- **R3: Structured Output, Model Parity & Dimension Isolation**
  - Structured-output agents (`PlannerAgent`, `ReviewerAgent`) must enforce schema validation when `MANGOMAS_AGENTS__<NAME>__VALIDATE_OUTPUT=true`. Under Gemini models, outputs must adhere to the negative prompt constraint ("Do not include markdown fences") and pass strict `model_validate_json` without markdown stripping.
  - To prevent vector dimension collision between 384-dimensional local embedding models (`all-MiniLM-L6-v2`) and 768-dimensional Vertex AI embeddings (`text-embedding-004`), Chroma persistence directories or collection names must remain strictly isolated (e.g., `MANGOMAS_VECTOR__COLLECTION=mangomas_vertex`, `MANGOMAS_VECTOR__PERSIST_DIR=./data/chroma_vertex`).
- **R4: Continuous Evaluation, CI Gating & Non-Destructive Guardrail**
  - The evaluation harness must support both heuristic scorers (`exact_match`, `contains`, `regex_match`, `json_keys`, `cost_budget`) and semantic scorers (`llm_judge`, `embedding`) powered by Vertex AI.
  - CI quality gates must enforce exit code 3 (`EVAL_GATE_EXIT_CODE`) upon threshold violations (`min_mean_score`, `min_pass_rate`, `fail_on_error`, `max_mean_score_drop`).
  - Operational Guardrail: Demonstrating local CLI capabilities must remain strictly non-destructive—no GCP infrastructure resources (Cloud Run services, Cloud SQL instances, Vertex AI endpoints) shall be created, modified, or deleted by demonstration commands.
- **Additive & Default-OFF**:
  - All GCP providers are opt-in via environment variables (`MANGOMAS_LLM__PROVIDER=vertex`, `MANGOMAS_EMBEDDINGS__PROVIDER=vertex`, `MANGOMAS_DB__PROVIDER=postgres`, `MANGOMAS_SECRETS__PROVIDER=gcp`, `MANGOMAS_TELEMETRY__EXPORTER=gcp`).
  - When unset or default, Mango-Mas maintains 100% byte-identical behavior with local LM Studio and SQLite storage. No source files in `src/` are modified.

## Scenarios (WHEN/THEN)

### Scenario 1: Local Vertex AI Agent Completion & Ambient ADC Authentication
- WHEN `MANGOMAS_LLM__PROVIDER=vertex` is configured and valid Application Default Credentials (ADC) are present on the local workstation via `gcloud auth application-default login` THEN `mangomas chat "Hello" -a chat` dispatches the turn to Vertex AI Gemini, outputs the generated response to stdout, and exits with code 0.
- WHEN `MANGOMAS_LLM__PROVIDER=vertex` is configured and ADC credentials are missing or expired THEN the command catches `DefaultCredentialsError`, translates it to typed `LLMUnavailable`, writes an error message to stderr, and terminates with exit code 1 (`EXIT_RUNTIME_ERROR`).

### Scenario 2: Structured Output Validation with Gemini Models
- WHEN `MANGOMAS_AGENTS__PLANNER__VALIDATE_OUTPUT=true` is enabled with `MANGOMAS_LLM__PROVIDER=vertex` and Gemini returns a valid JSON object matching the `ExecutionPlan` schema without markdown code blocks THEN `StructuredOutputAgent.handle` validates the payload successfully and returns the structured plan.
- WHEN Gemini or a local model emits markdown backticks (````json ... ````) or an invalid JSON payload under `VALIDATE_OUTPUT=true` THEN `self._schema.model_validate_json` raises a `ValidationError`, translated immediately into `LLMBadResponse` (status HTTP 502 / CLI exit 1), proving the validation guard is active and falsifiable.

### Scenario 3: Vertex AI Embeddings & Vector Search with Dimension Isolation
- WHEN `MANGOMAS_EMBEDDINGS__PROVIDER=vertex` and `MANGOMAS_VECTOR__COLLECTION=mangomas_vertex` are configured for document ingestion (`mangomas rag ingest <file>`) THEN text chunks are embedded as 768-dimensional vectors via `text-embedding-004`, upserted into Chroma, and subsequent queries (`mangomas rag query "<text>"`) return ranked results with cosine similarity scores in `[0.0, 1.0]`.
- WHEN 768-dimensional Vertex AI embeddings are written into an existing Chroma collection that was previously initialized with 384-dimensional local embeddings THEN `ChromaVectorStore` raises typed `PersistenceError` (wrapping Chroma's `InvalidArgumentError`), proving collection isolation is mandatory across distinct embedding models.

### Scenario 4: Turn Persistence to Cloud SQL Postgres via Proxy
- WHEN `MANGOMAS_DB__PROVIDER=postgres` is configured and the Cloud SQL Auth Proxy is running on `127.0.0.1:5432` THEN `mangomas chat "Persist this" -a chat` creates the `turns` table idempotently, persists the conversation turn as a JSONB record, and `mangomas history --limit 1` displays the persisted JSON turn.
- WHEN `MANGOMAS_DB__PROVIDER=postgres` is configured and the database endpoint is unreachable (proxy stopped) THEN `asyncpg` raises a connection failure, translated to typed `PersistenceError`, halting execution with exit code 1.

### Scenario 5: Secret Resolution via GCP Secret Manager in Strict Mode
- WHEN `MANGOMAS_SECRETS__PROVIDER=gcp` and `MANGOMAS_SECRETS__STRICT=true` are configured and a service-account JSON secret (`MANGOMAS_LLM__SECRET_REF` under `PROVIDER=vertex`), bearer token (`MANGOMAS_LLM__SECRET_REF` under `PROVIDER=lmstudio`), or API auth token (`MANGOMAS_AUTH__SECRET_REF` under `MANGOMAS_AUTH__ENABLED=true`) references a valid secret `projects/<PROJECT>/secrets/<NAME>/versions/latest` accessible via ADC THEN the secret payload is resolved securely in memory and redacted from all logs.
- WHEN a secret lookup fails due to permission denial, missing/invalid authentication, timeout, or API error in strict mode THEN `GCPSecretManagerProvider` raises typed `SecretsResolutionError` (`NotFound` returns `None` per ADR-002 / ADR-0010), failing closed immediately rather than silently degrading credentials.

### Scenario 6: Continuous Evaluation CI Quality Gate & Regression Enforcement
- WHEN `mangomas eval` executes against Vertex AI with `--gate --min-mean-score 0.8` using deterministic/`embedding` scorers (or a calibrated `llm_judge` via `MANGOMAS_EVAL__JUDGE_CALIBRATION_PATH`, per spec-0047 / spec-0048) and all test predictions score $\ge 0.8$ THEN the evaluation summary outputs passing metrics across all sinks and terminates with exit code 0.
- WHEN any gated prediction score drops below `--min-mean-score`, or when `--max-mean-score-drop` is exceeded relative to `--baseline` THEN the evaluation runner prints the regression delta, flushes all sinks, and terminates with exit code 3 (`EVAL_GATE_EXIT_CODE`).

### Scenario 7: OpenTelemetry Cloud Trace & Cloud Logging Export
- WHEN `MANGOMAS_TELEMETRY__EXPORTER=gcp` and `MANGOMAS_LOG__FORMAT=json` are configured THEN CLI executions stream OTel trace spans to Google Cloud Trace, and format stderr logs as JSON with GCP-compliant `severity`, `trace_id`, and `span_id`.
- WHEN `MANGOMAS_TELEMETRY__EXPORTER=console` (the default) is configured THEN trace spans are printed locally or suppressed, and no network calls to Cloud Trace are initiated.

## Config / env additions

| Env var (`MANGOMAS_*`) | Default | Demonstration Parity Value | Purpose |
|------------------------|---------|----------------------------|---------|
| `MANGOMAS_ENV` | `local` | `dev` | Environment label triggering development telemetry |
| `MANGOMAS_LOG_LEVEL` | `INFO` | `DEBUG` | Verbose diagnostic logging for CLI demonstration |
| `MANGOMAS_LOG__FORMAT` | `text` | `json` | Formats stdout/stderr as Cloud Logging JSON objects |
| `MANGOMAS_LLM__PROVIDER` | `lmstudio` | `vertex` | Switches LLM completion and streaming adapter to Vertex AI |
| `MANGOMAS_LLM__PROJECT_ID` | `None` | `<YOUR_GCP_PROJECT_ID>` | GCP project id hosting Vertex AI quotas |
| `MANGOMAS_LLM__LOCATION` | `us-central1` | `us-central1` | GCP region for Vertex AI endpoints |
| `MANGOMAS_LLM__MODEL` | `local-model` | `gemini-2.5-flash` | Selected Gemini foundation model (active GA) |
| `MANGOMAS_LLM__TEMPERATURE` | `0.2` | `0.2` | Model sampling temperature |
| `MANGOMAS_LLM__TIMEOUT_SECONDS` | `60.0` | `60.0` | httpx and async call timeout for LLM generation |
| `MANGOMAS_EMBEDDINGS__ENABLED` | `false` | `true` | Enables embedding generation subsystem |
| `MANGOMAS_EMBEDDINGS__PROVIDER` | `lmstudio` | `vertex` | Switches embedding client to Vertex AI |
| `MANGOMAS_EMBEDDINGS__PROJECT_ID` | `None` | `<YOUR_GCP_PROJECT_ID>` | GCP project id for Vertex AI embeddings |
| `MANGOMAS_EMBEDDINGS__LOCATION` | `us-central1` | `us-central1` | GCP region for Vertex AI embeddings |
| `MANGOMAS_EMBEDDINGS__MODEL` | `local-model` | `text-embedding-004` | 768-dimensional text embedding model |
| `MANGOMAS_EMBEDDINGS__BATCH_SIZE` | `32` | `32` | Maximum texts sent in a single embedding batch |
| `MANGOMAS_VECTOR__ENABLED` | `false` | `true` | Enables vector store repository for RAG |
| `MANGOMAS_VECTOR__PROVIDER` | `chroma` | `chroma` | Local ChromaDB vector repository |
| `MANGOMAS_VECTOR__PERSIST_DIR` | `./data/chroma` | `./data/chroma_vertex` | Isolated persistence directory for 768-dim embeddings |
| `MANGOMAS_VECTOR__COLLECTION` | `mangomas` | `mangomas_vertex` | Isolated collection name preventing dimension collisions |
| `MANGOMAS_DB__PROVIDER` | `sqlite` | `postgres` | Switches turn persistence to PostgreSQL / Cloud SQL |
| `MANGOMAS_DB__URL` | `sqlite:///./data/mangomas.db` | `postgresql://mangomas:<PASS>@127.0.0.1:5432/mangomas` | Cloud SQL Auth Proxy local DSN connection |
| `MANGOMAS_SECRETS__PROVIDER` | `env` | `gcp` | Switches secret resolution to GCP Secret Manager |
| `MANGOMAS_SECRETS__PROJECT_ID` | `None` | `<YOUR_GCP_PROJECT_ID>` | GCP project id hosting Secret Manager secrets |
| `MANGOMAS_SECRETS__STRICT` | `false` | `true` | Fails loud on secret resolution or permission errors |
| `MANGOMAS_AUTH__ENABLED` | `false` | `false` (opt-in in Journey 4: `true`) | Enforces authentication check via Secret Manager token |
| `MANGOMAS_AUTH__SECRET_REF` | `None` | `mangomas-api-token-dev` | Secret reference resolved via Secret Manager |
| `MANGOMAS_TELEMETRY__EXPORTER` | `console` | `gcp` | Exports distributed traces to Google Cloud Trace |
| `MANGOMAS_TELEMETRY__METRICS_ENABLED`| `false` | `false` (opt-in in Journey 5: `true`) | Emits OpenTelemetry agent metrics |
| `MANGOMAS_AGENTS__PLANNER__VALIDATE_OUTPUT` | `false` | `true` | Enforces strict JSON schema validation on PlannerAgent |
| `MANGOMAS_AGENTS__REVIEWER__VALIDATE_OUTPUT` | `false` | `true` | Enforces strict JSON schema validation on ReviewerAgent |

All tunables represent existing Pydantic fields in `src/mangomas/config/` surfaced through `Settings`—**no hard-coded values**.

## Protocol / contract impact

- **New/changed protocols:** _none — existing protocols are satisfied 100%:_
  - `LLMClient`, `StreamingLLMClient`, `PingableLLMClient` in `src/mangomas/adapters/llm/base.py`
  - `EmbeddingClient` in `src/mangomas/adapters/embeddings/base.py`
  - `TurnRepository` in `src/mangomas/adapters/storage/base.py`
  - `SecretsProvider` in `src/mangomas/secrets/provider.py`
  - `Scorer` in `src/mangomas/eval/protocol.py`
- **New error types:** _none — existing typed errors are reused:_
  - `LLMBadResponse`, `LLMTimeout`, `LLMUnavailable` in `src/mangomas/errors.py`
  - `SecretsResolutionError` in `src/mangomas/errors.py`
  - `PersistenceError` in `src/mangomas/errors.py`
- **Registry additions:** _none — all providers are pre-registered in existing registries:_
  - `llm_registry`: `"vertex"` -> `VertexClient`
  - `embeddings_registry`: `"vertex"` -> `VertexEmbeddingClient`
  - `storage_registry`: `"postgres"` -> `PostgresRepository`
  - `secrets_registry`: `"gcp"` -> `GCPSecretManagerProvider`
- **Composition root:** No changes needed. `src/mangomas/composition/` already resolves all GCP adapters when selected by `Settings`.

## Backwards-compatibility

- **Default State**: When GCP environment variables are absent, Mango-Mas defaults to `lmstudio` for LLM and embeddings, `sqlite` for storage, `env` for secrets, and `console` for telemetry. Local execution remains 100% byte-identical.
- **Packaging Isolation**: Cloud SDKs (`google-cloud-aiplatform`, `google-cloud-secret-manager`, `opentelemetry-exporter-gcp-trace`, `asyncpg`) are packaged under the optional `[cloud]` extra. Installation of the base package without extras remains lightweight and functional.
- **Zero Source Modifications**: No application code in `src/` requires alteration to support this demonstration.

## Test plan

- **Unit Tests**:
  - `tests/test_vertex_unit.py`: Verifies `VertexClient` initialization, role folding (`[system]`), lazy SDK imports, and error translation without live network calls.
  - `tests/adapters/embeddings/test_vertex_embeddings.py`: Verifies `VertexEmbeddingClient` batch handling and mixin contracts.
  - `tests/test_postgres.py`: Verifies `PostgresRepository` query building, connection pool handling, and JSON codec serialization.
  - `tests/test_secrets_gcp.py`: Verifies `GCPSecretManagerProvider` resource path construction, strict mode exceptions, and logging redaction.
  - `tests/test_cli_surface.py`: Verifies CLI commands (`agents`, `chat`, `eval`, `history`, `rag`, `workflow`) and argument parsing.
  - `tests/deploy/test_env_example_contract.py`: Asserts bidirectional schema alignment between `.env.example`, `.env.gcp-demo`, and Pydantic `Settings`.
- **Gated Live Integration Tests**:
  - `RUN_VERTEX=1 VERTEX_PROJECT_ID=<ID> pytest tests/vertex/test_smoke.py tests/vertex/test_embeddings.py`: Live generation, streaming, and embeddings against Vertex AI Gemini and `text-embedding-004`.
  - `RUN_POSTGRES=1 pytest tests/postgres/test_postgres_smoke.py`: Live turn insertion and retrieval against PostgreSQL.
  - `RUN_GCP_SECRETS=1 GCP_SECRETS_PROJECT=<ID> pytest tests/integration/test_gcp_secrets_live.py`: Live secret version resolution from GCP Secret Manager.
  - `RUN_GCP_TRACE=1 pytest tests/test_telemetry.py tests/test_telemetry_gcp_contract.py -m gcp_trace`: Live OTel span export to Google Cloud Trace.
- **Falsifiability & Gate Proofs**:
  - The CI quality gate in `mangomas eval` must terminate with exit code 3 when thresholds are violated, and exit code 0 when satisfied (Scenario 6).
  - Validation enforcement in `PlannerAgent` must reject fenced markdown with `LLMBadResponse` and accept valid JSON (Scenario 2).
- **Coverage**: Maintains the global 98% test coverage floor across the repository.

## Acceptance criteria

Offline-verified items are proven by the default `make gate` run. **Live** items
need a billed project; the 2026-11-09 operator attempt was blocked by a closed
billing account (`PERMISSION_DENIED` on every Vertex call), so they stay open
until a consented live run captures evidence.

- [x] `.env.gcp-demo` exists, is tracked, and its names and values validate against `Settings` (`tests/deploy/test_env_example_contract.py`).
- [x] Read-only pre-flight (`make gcp-preflight`) covers ADC, project, billing enabled **and account open**, required APIs, Postgres endpoint (`tests/test_preflight_verification.py`).
- [x] Cloud Run manifests in `deploy/` render with zero unrendered placeholders (`tests/deploy/test_manifest_render_contract.py`).
- [x] Eval quality gate exits 3 on every gate dimension (`tests/eval/test_eval_gate_falsifiability.py`).
- [x] Cloud Logging JSON schema and W3C trace-context propagation (`tests/test_telemetry_gcp_contract.py`; exporter cases under `make gcp-trace`).
- [x] Zero GCP infrastructure resources created or modified (R4).
- [ ] **Live:** `mangomas chat` / `planner` / `reviewer` against Vertex Gemini with `VALIDATE_OUTPUT=true`.
- [ ] **Live:** `mangomas rag ingest|query` with Vertex embeddings in an isolated collection.
- [ ] **Live:** `mangomas history` over turns persisted to Cloud SQL through the Auth Proxy (offline parity: `tests/postgres/test_postgres_history_contract.py`, `RUN_POSTGRES`).
- [ ] **Live:** strict Secret Manager resolution and spans visible in Cloud Trace.
