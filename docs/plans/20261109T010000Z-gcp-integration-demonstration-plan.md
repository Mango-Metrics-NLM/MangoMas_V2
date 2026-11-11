# GCP Local CLI Integration Demonstration Delivery Plan

- **Branch:** `sdlc/gcp-demo-20261109` (from `origin/feat/initial-release@27527f4`)
- **Date:** 2026-11-09
- **Target release:** Unreleased
- **Status:** Offline deliverables complete; live journeys open (blocked on 2026-11-09 by a closed billing account — see spec-0049 acceptance criteria)
- **Specs:** spec-0049
- **ADRs:** _none_

> **Reconciliation note (2026-11-09).** This plan was first drafted on a branch
> 86 commits behind trunk, under the colliding number spec-0039. It was
> renumbered and ported. The pre-flight was then redesigned: billing-account and
> API checks were added, defaults now come from `Settings`, and `google-auth` is
> injected. The sprint-named adversarial suite was removed after a coverage diff
> showed it covered no line or branch the trunk suite did not already cover. The
> sprint "[x] Done" markers below record the original sprint bookkeeping. They
> are not live-run evidence.

---

## Executive summary

This delivery plan operationalizes the complete, end-to-end demonstration of Mango-Mas on Google Cloud Platform (GCP) through local CLI workflows, mirroring local LM Studio capabilities with zero application code modifications. The sequencing is structured into three iterative sprints (PR 1: Developer Experience & Ambient GCP Foundations, PR 2: Knowledge Retrieval, Enterprise Persistence & Continuous Evaluation, and PR 3: Distributed Observability, Workflows & Cloud Run Validation) totaling 41 Story Points across 5 Epics and 8 User Stories. The primary constraint shaping this sequencing is the **R4 Non-Destructive Guardrail**: all cloud interactions execute through local CLI client adapters targeting existing cloud APIs via ambient Application Default Credentials (ADC) and local proxy tunnels, creating zero cloud infrastructure drift while strictly proving contract and protocol parity.

---

## 1. Multi-Perspective Architectural & Scientific Analysis

### 1.1 SDLC & Cloud Architecture Perspective

The Mango-Mas architecture enforces strict decoupling between high-level agent domains (`src/mangomas/core/`) and external infrastructure adapters (`src/mangomas/adapters/`) via `@runtime_checkable` Python Protocols (`LLMClient`, `EmbeddingClient`, `TurnRepository`, `SecretsProvider`, `Scorer`).

1. **Ambient Application Default Credentials (ADC)**:
   - In adherence with Google Cloud security best practices (ADR-0001, ADR-0002), all GCP adapters (`VertexClient`, `VertexEmbeddingClient`, `GCPSecretManagerProvider`) authenticate via ambient Application Default Credentials discovered from `%APPDATA%\gcloud\application_default_credentials.json` (Windows) or `~/.config/gcloud/application_default_credentials.json` (Linux/macOS).
   - Long-lived service account JSON keys are explicitly rejected in `VertexEmbeddingClient` and `GCPSecretManagerProvider`, eliminating secret leakage on developer laptops.
2. **Postgres / Cloud SQL Connectivity via Auth Proxy**:
   - `PostgresRepository` connects via `asyncpg` connection pools.
   - For developer workstations, connecting to Cloud SQL requires running the local Cloud SQL Auth Proxy (`./cloud-sql-proxy <PROJECT>:<REGION>:<INSTANCE> --port 5432`), establishing an encrypted, IAM-authenticated mTLS tunnel to `127.0.0.1:5432`.
   - On connection initialization, `PostgresRepository` idempotently executes schema definitions (`turns` table) and JSONB codecs, providing 100% data shape parity with `SQLiteRepository`.
3. **Fail-Closed Secret Management**:
   - `GCPSecretManagerProvider` operates in strict mode (`MANGOMAS_SECRETS__STRICT=true`), raising typed `SecretsResolutionError` (HTTP 503 / CLI exit 2) on permission, authentication, timeout, or API failures (`NotFound` returns `None` per ADR-002 / ADR-0010), ensuring zero silent authorization bypasses.
   - Logging redaction guarantees that secret resource names and versions are scrubbed before writing to logs.
4. **OpenTelemetry Telemetry Pipeline**:
   - When `MANGOMAS_TELEMETRY__EXPORTER=gcp` is set, `configure_telemetry()` registers `CloudTraceSpanExporter` from `opentelemetry-exporter-gcp-trace`.
   - `JsonFormatter` formats stdout/stderr into Cloud Logging JSON objects with automatically injected `trace_id` and `span_id` fields.
5. **Cloud Run Manifest Validation**:
   - Deployment manifests in `deploy/service.yaml` and overlays in `deploy/environments.yaml` are verified offline via `scripts/deploy_environment.py` to prevent placeholder omissions or missing environment bindings.

### 1.2 AI/ML Science & Evaluation Parity Perspective

1. **Model Parity: LM Studio (`local-model`) vs Vertex AI Gemini**:
   - Local instruction-tuned models (e.g., Llama-3.1-8B, Qwen-2.5-7B) running in LM Studio are hardware-constrained (context windows $\le 32\text{K}$ tokens, output caps $2\text{K}$–$4\text{K}$ tokens) and frequently violate negative prompt constraints by wrapping JSON in markdown fences (````json ... ````).
   - Vertex AI Gemini 2.5 Flash (`gemini-2.5-flash`, active GA) provides a $1\text{M}$ token context window, $8\text{K}$ token output budget, and superior instruction adherence for complex reasoning.
2. **Prompt & System Role Folding**:
   - `VertexClient` translates incoming chat messages to Gemini's native API: `assistant` maps to `model`, while `user`, `system`, and `tool` map to `user`. Non-user messages are prefixed with `[<role>] <content>` (system messages are folded as `[system] <content>`).
3. **Structured Output Validation (`VALIDATE_OUTPUT=true`)**:
   - When enabled on `PlannerAgent` or `ReviewerAgent`, `StructuredOutputAgent.handle` calls `self._schema.model_validate_json(content)`.
   - Because `model_validate_json` enforces strict JSON without regex recovery, Gemini's strict adherence eliminates the `LLMBadResponse` failures common on local 7B models.
4. **Embedding Dimensions & Chroma Collection Isolation**:
   - Local `sentence-transformers` (`all-MiniLM-L6-v2`) produces 384-dimensional vectors with sequence lengths up to 256/512 tokens.
   - Vertex AI `text-embedding-004` produces 768-dimensional vectors with sequence lengths up to 2,048 tokens.
   - Because ChromaDB collections permanently lock their dimensionality upon creation, switching between local and Vertex embeddings requires separate collection names and persistence directories (`MANGOMAS_VECTOR__COLLECTION=mangomas_vertex`, `MANGOMAS_VECTOR__PERSIST_DIR=./data/chroma_vertex`).
5. **Continuous Evaluation Harness (`mangomas eval`)**:
   - Full evaluation parity is supported across both heuristic scorers (`exact_match`, `regex_match`, `contains`, `json_keys`, `cost_budget`) and semantic scorers (`llm_judge`, `embedding`).
   - Automated CI quality gates (`--gate`, `--min-mean-score`, `--min-pass-rate`, `--max-mean-score-drop`) enforce strict termination with exit code 3 (`EVAL_GATE_EXIT_CODE`) upon regressions.

### 1.3 Product Perspective & Developer Journeys

1. **Target Personas**:
   - **AI Application Engineer**: Needs rapid local prototyping with LM Studio and zero cloud costs, followed by instantaneous local CLI execution against Vertex AI Gemini without altering agent code or schemas.
   - **Cloud DevOps / SRE**: Demands zero leaked service account keys on workstations, ambient ADC authentication, Cloud SQL Proxy connectivity, strict Secret Manager resolution, and OpenTelemetry trace exports.
   - **Product Evaluator / AI Quality Auditor**: Requires reproducible evaluation datasets, automated LLM-as-a-judge scoring with Gemini, regression tracking, and CI/CD quality gate enforcement.
2. **Unified Value Proposition**:
   > *"Local-First Development with Instant Cloud Parity: Architect and debug multi-agent workflows and RAG pipelines entirely on-device using LM Studio and SQLite, then execute and evaluate against enterprise Google Cloud services (Vertex AI, Cloud SQL, Secret Manager, Cloud Trace) from the exact same local CLI by toggling `MANGOMAS_*` environment variables."*

---

## 2. Operational Runbook: Local CLI Demonstration

### 2.1 Developer Onboarding & IAM Prerequisites

Before executing the local CLI demonstration, ensure your GCP user account (`user:<developer-email>`) possesses the following IAM roles in the target project:

```bash
# IAM Roles Required for Local CLI Demonstration
roles/aiplatform.user                  # Vertex AI Gemini & Text Embeddings
roles/secretmanager.secretAccessor     # Secret Manager runtime secret resolution
roles/cloudsql.client                  # Cloud SQL Auth Proxy client connection
roles/cloudtrace.agent                 # OpenTelemetry Cloud Trace span export
roles/serviceusage.serviceUsageConsumer # API quota consumption
```

One-time project API enablement:
```bash
gcloud services enable \
    aiplatform.googleapis.com \
    secretmanager.googleapis.com \
    cloudtrace.googleapis.com \
    sqladmin.googleapis.com \
    run.googleapis.com \
    --project="<YOUR_GCP_PROJECT_ID>"
```

Workstation authentication:
```powershell
gcloud auth login
gcloud auth application-default login
gcloud config set project <YOUR_GCP_PROJECT_ID>
gcloud auth application-default print-access-token | Out-Null; if ($?) { "ADC token OK" }  # never print the bearer token
```

Install repository with cloud and RAG extras (`gcp-monitoring` is optional and needed only when `MANGOMAS_TELEMETRY__METRICS_ENABLED=true`):
```powershell
pip install -e ".[dev,cloud,rag]"
pip install -e ./mango-integration-contracts
```

---

### 2.2 End-to-End Local CLI Demonstration User Journeys

#### Journey 1: Local CLI Agent Execution targeting Vertex AI Gemini
Demonstrates interactive single-turn chat, multi-agent planning, and review using Gemini 2.5 Flash.

```powershell
# Configure Vertex AI environment
$env:MANGOMAS_ENV = "local"
$env:MANGOMAS_LLM__PROVIDER = "vertex"
$env:MANGOMAS_LLM__PROJECT_ID = "<YOUR_GCP_PROJECT_ID>"
$env:MANGOMAS_LLM__LOCATION = "us-central1"
$env:MANGOMAS_LLM__MODEL = "gemini-2.5-flash"
$env:MANGOMAS_LLM__TEMPERATURE = "0.2"

# 1. Verify registered agents
mangomas agents

# 2. Execute single-turn chat
mangomas chat "Explain the architectural difference between local LM Studio and Vertex AI in Mango-Mas in two concise sentences." -a chat

# 3. Execute structured planning agent with schema validation
$env:MANGOMAS_AGENTS__PLANNER__VALIDATE_OUTPUT = "true"
mangomas chat "Generate a 3-step migration plan for transitioning turn persistence to Cloud SQL" -a planner --verbose

# 4. Execute reviewer agent with schema validation
$env:MANGOMAS_AGENTS__REVIEWER__VALIDATE_OUTPUT = "true"
mangomas chat "Review proposal: Disable database transaction logging to speed up chat completions." -a reviewer --verbose
```

#### Journey 2: Local CLI RAG Pipeline with Vertex AI Embeddings + Chroma
Demonstrates document chunking, embedding via `text-embedding-004`, vector storage, and semantic retrieval.

```powershell
# Configure Vertex AI Embeddings and isolated Chroma store
$env:MANGOMAS_EMBEDDINGS__ENABLED = "true"
$env:MANGOMAS_EMBEDDINGS__PROVIDER = "vertex"
$env:MANGOMAS_EMBEDDINGS__PROJECT_ID = "<YOUR_GCP_PROJECT_ID>"
$env:MANGOMAS_EMBEDDINGS__LOCATION = "us-central1"
$env:MANGOMAS_EMBEDDINGS__MODEL = "text-embedding-004"
$env:MANGOMAS_VECTOR__ENABLED = "true"
$env:MANGOMAS_VECTOR__PROVIDER = "chroma"
$env:MANGOMAS_VECTOR__PERSIST_DIR = "./data/chroma_vertex"
$env:MANGOMAS_VECTOR__COLLECTION = "mangomas_vertex"

# 1. Ingest repository documentation
mangomas rag ingest README.md

# 2. Query semantic knowledge base
mangomas rag query "How does Mango-Mas support Vertex AI and Cloud SQL?" --top-k 3
```

#### Journey 3: State Persistence with Cloud SQL & GCP Secret Manager
Demonstrates turn persistence to PostgreSQL and fail-closed secret resolution.

```powershell
# 1. Start Cloud SQL Auth Proxy in background terminal:
# cloud-sql-proxy <YOUR_GCP_PROJECT_ID>:us-central1:<INSTANCE_NAME> --port 5432

# 2. Configure Cloud SQL storage and GCP Secret Manager
$env:MANGOMAS_DB__PROVIDER = "postgres"
$env:MANGOMAS_DB__URL = "postgresql://mangomas:<DB_PASSWORD>@127.0.0.1:5432/mangomas"
$env:MANGOMAS_SECRETS__PROVIDER = "gcp"
$env:MANGOMAS_SECRETS__PROJECT_ID = "<YOUR_GCP_PROJECT_ID>"
$env:MANGOMAS_SECRETS__STRICT = "true"

# 3. Execute a turn persisted directly to Cloud SQL Postgres
mangomas chat "Store this conversation turn directly in Cloud SQL Postgres" -a chat

# 4. Inspect persisted turns from Cloud SQL
mangomas history --limit 3
```

#### Journey 4: Continuous Evaluation Harness against Vertex AI (`mangomas eval`)
Demonstrates deterministic quality gating and LLM-as-a-judge scoring powered by Vertex AI (under spec-0047, `llm_judge` score thresholds report without gating unless calibrated via `MANGOMAS_EVAL__JUDGE_CALIBRATION_PATH`).

```powershell
# 1. Run LLM-as-a-judge evaluation with Gemini
mangomas eval -d tests/eval/fixtures/all_pass.jsonl -s llm_judge -a chat --gate --min-mean-score 0.7 -o eval-output/vertex_eval_report.json

# 2. Run semantic embedding similarity evaluation
mangomas eval -d tests/eval/fixtures/all_pass.jsonl -s embedding -a chat -o eval-output/vertex_embedding_eval.json

# 3. Simulate CI regression gate enforcement (exit code 3 on failure)
mangomas eval -d tests/eval/fixtures/mixed.jsonl -s exact_match -a chat --gate --min-mean-score 1.0 --baseline eval-output/vertex_eval_report.json --max-mean-score-drop 0.0
```

#### Journey 5: Cloud Telemetry Verification & Cloud Run Manifest Validation
Demonstrates OpenTelemetry trace export to Cloud Trace, Cloud Logging JSON formatting, and deployment manifest checking.

```powershell
# 1. Stream traces to Cloud Trace and format logs as JSON
$env:MANGOMAS_TELEMETRY__EXPORTER = "gcp"
$env:MANGOMAS_LOG__FORMAT = "json"
mangomas chat "Trace this turn into Google Cloud Trace" -a chat -v

# 2. Offline validation of Cloud Run deployment manifests
python scripts/deploy_environment.py --registry deploy/environments.yaml validate

# 3. Render environment overlay for Cloud Run
$env:PROJECT_ID = "<YOUR_GCP_PROJECT_ID>"
$env:LLM_MODEL = "gemini-2.5-flash"
python scripts/deploy_environment.py --registry deploy/environments.yaml render --environment dev --image gcr.io/<YOUR_GCP_PROJECT_ID>/mangomas:dev --output rendered-service.yaml --substitute-from-env PROJECT_ID --substitute-from-env LLM_MODEL
```

---

### 2.3 Troubleshooting Decision Matrix

| Symptom / Error | Root Cause | Diagnostic Command | Remediation Action |
|---|---|---|---|
| `DefaultCredentialsError` / `Missing ADC` | Missing or expired gcloud ADC token on local workstation. | `gcloud auth application-default print-access-token >/dev/null && echo ok` (never print the token) | Run `gcloud auth application-default login`. Fallback: set `MANGOMAS_LLM__PROVIDER=lmstudio` for offline work. |
| `VertexError (LLMBadResponse)` 404 | Model name invalid or region unsupported. | `gcloud ai models list --region=us-central1` | Set `MANGOMAS_LLM__MODEL=gemini-2.5-flash` and `MANGOMAS_LLM__LOCATION=us-central1`. |
| `ResourceExhausted (429)` | Vertex AI API quota rate limit exceeded. | Review Cloud Console Vertex AI Quotas. | Set `--parallelism 1` in `eval`, increase `MANGOMAS_LLM__TIMEOUT_SECONDS=120`, or switch to `gemini-2.5-flash`. |
| `PersistenceError` (wrapping Chroma `InvalidArgumentError`) | 768-dim embeddings written to 384-dim collection. | Inspect `./data/chroma/` metadata. | Set `MANGOMAS_VECTOR__COLLECTION=mangomas_vertex` and `MANGOMAS_VECTOR__PERSIST_DIR=./data/chroma_vertex`. |
| `SecretsResolutionError` (PermissionDenied) | Missing `roles/secretmanager.secretAccessor` IAM role. | `gcloud secrets versions access latest --secret=<NAME>` | Grant role to user account, or set `MANGOMAS_SECRETS__STRICT=false` for local fallback. |
| `asyncpg.CannotConnectNowError` | Cloud SQL Proxy is not running on port 5432. | `Test-NetConnection -ComputerName 127.0.0.1 -Port 5432` | Start `cloud-sql-proxy <PROJECT>:<REGION>:<INSTANCE> --port 5432`. Fallback: `MANGOMAS_DB__PROVIDER=sqlite`. |
| CLI command hangs during exit | Telemetry exporter awaiting network flush. | Check stderr logs for OTel exporter errors. | Fallback: set `MANGOMAS_TELEMETRY__EXPORTER=console` to bypass cloud network latency. |

---

## 3. Scrum Delivery Backlog

### 3.1 Definition of Ready (DoR) and Definition of Done (DoD)

- **Definition of Ready (DoR)**:
  1. Story articulates user role, functional capability, and business value.
  2. Acceptance criteria are fully written in Given/When/Then (Gherkin) format.
  3. Environment variable bindings (`MANGOMAS_*`), CLI flags, and dependency extras are identified.
  4. External GCP dependencies, required IAM permissions, and fallbacks are documented.
  5. Sized in Story Points by the team, with non-blocking dependencies.
- **Definition of Done (DoD)**:
  1. CLI commands execute successfully against GCP endpoints with 100% parity to local LM Studio workflows.
  2. Zero repository application code modifications made in `src/`.
  3. Automated unit, integration, and gated tests pass (`pytest`, `ruff`, `mypy`).
  4. Demonstration runbook verified in both PowerShell (Windows) and Bash (Linux/macOS).
  5. The global 98% test coverage floor is preserved.

---

### 3.2 Epics and User Stories

#### Epic 1: Developer Experience & Ambient GCP Onboarding (DX)
* **Story 1.1: Standardized GCP Demonstration Configuration Template (`.env.gcp-demo`)** — [x] (COMPLETED — Sprint 1)
  - *Description*: As a developer, I want a dedicated `.env.gcp-demo` template file and documentation so that I can configure my local environment for GCP parity without modifying `.env.example`.
  - *Acceptance Criteria*:
    - **Given** `.env.gcp-demo` exists in the repository,
    - **When** compared against `src/mangomas/config/`,
    - **Then** every variable must resolve to a valid field on `Settings`, and running `tests/deploy/test_env_example_contract.py` validates schema adherence.
  - *Estimate*: 3 SP | *Dependencies*: None.
* **Story 1.2: Local ADC & Cloud SQL Pre-flight Verification Script** — [x] (COMPLETED — Sprint 1)
  - *Description*: As an operator, I want a pre-flight script (`scripts/verify_gcp_prerequisites.py`) that checks ADC validity, enabled GCP APIs, and Cloud SQL reachability before running CLI demos.
  - *Acceptance Criteria*:
    - **Given** ADC is unauthenticated,
    - **When** the pre-flight script runs,
    - **Then** it exits with code 1 and prints the exact remediation command `gcloud auth application-default login`.
  - *Estimate*: 3 SP | *Dependencies*: Story 1.1.

#### Epic 2: Core Agent & Model Parity on Vertex AI (Local CLI)
* **Story 2.1: Demonstration Runbook for Local CLI Chat & Structured Agents targeting Vertex AI** — [x] (COMPLETED — Sprint 1)
  - *Description*: As an AI Engineer, I want to execute `chat`, `planner`, and `reviewer` agents via `mangomas chat` against Vertex AI Gemini models with structured JSON validation enabled.
  - *Acceptance Criteria*:
    - **Given** `MANGOMAS_LLM__PROVIDER=vertex` and `MANGOMAS_AGENTS__PLANNER__VALIDATE_OUTPUT=true`,
    - **When** executing `mangomas chat "Plan deployment" -a planner`,
    - **Then** the output is strictly valid JSON matching `ExecutionPlan`, logged on stdout, with exit code 0.
  - *Estimate*: 5 SP | *Dependencies*: Story 1.1.
* **Story 2.2: ToolAgent Multi-Step Reasoning with Vertex AI** — [x] (COMPLETED — Sprint 3)
  - *Description*: As an AI Engineer, I want `ToolAgent` to execute multi-step tool calls against Vertex AI up to the configured `max_tool_steps`.
  - *Acceptance Criteria*:
    - **Given** `MANGOMAS_LLM__PROVIDER=vertex`, RAG enabled (`MANGOMAS_EMBEDDINGS__ENABLED=true` and `MANGOMAS_VECTOR__ENABLED=true`), and `MANGOMAS_AGENTS__TOOL__MAX_TOOL_STEPS=5`,
    - **When** `mangomas chat "Using the knowledge base, explain how Mango-Mas swaps local providers for GCP ones." -a tool` is executed,
    - **Then** the agent invokes the `retrieve` (`RetrievalTool`) entry in the tool registry, executes within budget, and returns a grounded answer over the ingested RAG corpus.
  - *Estimate*: 5 SP | *Dependencies*: Story 2.1.

#### Epic 3: Cloud-Native Knowledge Retrieval (Vertex Embeddings + Vector Store)
* **Story 3.1: Local CLI RAG Ingestion & Semantic Retrieval via Vertex AI Embeddings** — [x] (COMPLETED — Sprint 2)
  - *Description*: As an AI Engineer, I want to ingest documents and query context via `mangomas rag ingest` and `mangomas rag query` using `TextEmbeddingModel` on Vertex AI.
  - *Acceptance Criteria*:
    - **Given** `MANGOMAS_EMBEDDINGS__ENABLED=true`, `MANGOMAS_EMBEDDINGS__PROVIDER=vertex`, and `MANGOMAS_VECTOR__ENABLED=true`,
    - **When** `mangomas rag ingest <sample_doc>` is executed,
    - **Then** chunks are generated, embedded in batches via Vertex AI async API, stored in Chroma, and retrievable via `mangomas rag query` with cosine ranking.
  - *Estimate*: 5 SP | *Dependencies*: Story 1.1.

#### Epic 4: Enterprise State & Secret Management (Cloud SQL + Secret Manager)
* **Story 4.1: Cloud SQL Turn Persistence via Local CLI with Multi-Tenancy** — [x] (COMPLETED — Sprint 2)
  - *Description*: As an SRE, I want local CLI conversations to persist into Cloud SQL Postgres with tenant partitioning, verifiable via `mangomas history`.
  - *Acceptance Criteria*:
    - **Given** `MANGOMAS_DB__PROVIDER=postgres` and Cloud SQL Auth Proxy running,
    - **When** an agent turn is completed,
    - **Then** `mangomas history --limit 1` displays the persisted JSON turn, and the Postgres `turns` table contains the matching record.
  - *Estimate*: 5 SP | *Dependencies*: Story 1.2, 2.1.
* **Story 4.2: Strict GCP Secret Manager Resolution & Fail-Closed Auth** — [x] (COMPLETED — Sprint 1)
  - *Description*: As an SRE, I want `MANGOMAS_SECRETS__STRICT=true` to resolve API keys and bearer tokens from Secret Manager, failing loud if permissions are denied.
  - *Acceptance Criteria*:
    - **Given** `MANGOMAS_SECRETS__PROVIDER=gcp` and `MANGOMAS_SECRETS__STRICT=true`,
    - **When** an invalid secret ref is requested,
    - **Then** the application raises `SecretsResolutionError` and exits with an explicit error code.
  - *Estimate*: 3 SP | *Dependencies*: Story 1.1.

#### Epic 5: Continuous Evaluation & CI/CD Quality Gating (Local Eval + Cloud Run)
* **Story 5.1: Automated Vertex AI Evaluation Harness with CI Quality Gate Enforcement** — [x] (COMPLETED — Sprint 2)
  - *Description*: As an AI Quality Auditor, I want to run `mangomas eval` targeting Vertex AI with gating on `mean_score`, `pass_rate`, and baseline regression drops.
  - *Acceptance Criteria*:
    - **Given** an evaluation dataset and baseline report,
    - **When** a score drops below `--min-mean-score` or drops vs baseline,
    - **Then** the command terminates with exit code 3 (`EVAL_GATE_EXIT_CODE`) after all sinks emit.
  - *Estimate*: 5 SP | *Dependencies*: Story 2.1.
* **Story 5.2: Distributed Cloud Tracing & Cloud Run Deployment Verification** — [x] (COMPLETED — Sprint 3)
  - *Description*: As an SRE, I want CLI execution to stream OpenTelemetry spans to Google Cloud Trace, format JSON logs, and smoke-probe deployed Cloud Run revisions.
  - *Acceptance Criteria*:
    - **Given** `MANGOMAS_TELEMETRY__EXPORTER=gcp` and `MANGOMAS_LOG__FORMAT=json`,
    - **When** executing CLI commands,
    - **Then** stderr contains GCP-compliant JSON logs with trace/span IDs, and spans appear in the Google Cloud Trace console.
  - *Estimate*: 5 SP | *Dependencies*: Story 1.1, 2.1.

---

### 3.3 Sprint Schedule & Milestones (41 Story Points)

```
Sprint 1: Developer Experience & Ambient GCP Foundations (14 SP) — [x] COMPLETED
├── Story 1.1: Standardized .env.gcp-demo Configuration Template (3 SP) — [x] Done
├── Story 1.2: Local ADC & Cloud SQL Pre-flight Verification Script (3 SP) — [x] Done
├── Story 2.1: Demonstration Runbook for Local CLI Chat & Structured Agents (5 SP) — [x] Done
└── Story 4.2: Strict GCP Secret Manager Resolution & Fail-Closed Auth (3 SP) — [x] Done
    └── Milestone M1: Core Vertex AI & Secrets Parity Achieved (Day 5) — [x] Completed

Sprint 2: Knowledge Retrieval, Enterprise Persistence & Continuous Evaluation (15 SP) — [x] COMPLETED
├── Story 3.1: Local CLI RAG Ingestion & Semantic Retrieval via Vertex AI (5 SP) — [x] Done
├── Story 4.1: Cloud SQL Turn Persistence via Local CLI (5 SP) — [x] Done
└── Story 5.1: Automated Vertex AI Evaluation Harness with CI Quality Gate (5 SP) — [x] Done
    └── Milestone M2: Knowledge & State Parity Validated (Day 10) — [x] Completed

Sprint 3: Distributed Observability, Workflows & Cloud Run Validation (12 SP) — [x] COMPLETED
├── Story 2.2: ToolAgent Multi-Step Reasoning with Vertex AI (5 SP) — [x] Done
├── Story 5.2: Distributed Cloud Tracing & Cloud Run Deployment Verification (5 SP) — [x] Done
└── Final Polish: Documentation & Runbook Verification (2 SP) — [x] Done
    └── Milestone M3: Full GCP Parity & Operational Production Gate (Day 15) — [x] Completed
```

---

### 3.4 Risk Management Matrix

| Risk ID | Risk Description | Probability / Impact | Trigger / Early Indicator | Mitigation Strategy | Owner |
|---|---|---|---|---|---|
| **RSK-01** | Developer workstation lacks gcloud ADC or token expires mid-demo. | Med / High | `DefaultCredentialsError` in CLI output. | Automated pre-flight check in `scripts/verify_gcp_prerequisites.py`; clear documentation to run `gcloud auth application-default login`. | DevOps Lead |
| **RSK-02** | Vertex AI API rate limiting (429) during parallel batch evaluation runs. | Med / Med | HTTP 429 / `ResourceExhausted` during `mangomas eval`. | Default `--parallelism 1` in demo configurations; implement exponential backoff; support model fallback. | AI/ML Lead |
| **RSK-03** | Cloud SQL connectivity blocked by network firewalls or missing proxy. | Med / High | Connection refused on `127.0.0.1:5432`. | Package Cloud SQL Auth Proxy startup script; document fallback to local SQLite (`MANGOMAS_DB__PROVIDER=sqlite`). | DevOps Lead |
| **RSK-04** | Unexpected cloud infrastructure billing costs during extensive evaluations. | Low / Med | Spikes in Gemini API usage or Cloud Trace ingestion. | Pair `--parallelism 1` with post-run cost gate `--max-mean-cost-usd 0.05` over `cost_budget` rows in `mangomas eval`; default to `gemini-2.5-flash`; set GCP billing alerts. | Product Lead |
| **RSK-05** | Cloud Trace OpenTelemetry exporter latency causing CLI command delays. | Low / Low | CLI commands taking >5s to exit after completion. | Lazy loading of exporters; flush timeouts bounded to 5s; fallback to `MANGOMAS_TELEMETRY__EXPORTER=console`. | SRE Lead |

---

## 4. PR Delivery Plan & Milestones

### PR 1 — DX, Ambient Identity & Core Model Parity (spec-0049) — [x] DELIVERED

#### Milestone A0 — Standardized GCP Demonstration Configuration & Schema Contract — [x] Completed
- **Failing test first:** `tests/deploy/test_env_example_contract.py` extended to validate `.env.gcp-demo` against `Settings` fields.
- **Depends on:** Nothing — parallel-safe entry point.
- **Deliverables:**
  - Create `.env.gcp-demo` containing full GCP demonstration configuration bindings.
  - Update `tests/deploy/test_env_example_contract.py` to assert `.env.gcp-demo` ⊆ `Settings`.

#### Milestone A1 — Local Pre-Flight Verification & Ambient ADC Validation — [x] Completed
- **Failing test first:** `tests/test_preflight_verification.py` verifying detection of missing ADC credentials and proxy endpoints.
- **Depends on:** Milestone A0.
- **Deliverables:**
  - Create `scripts/verify_gcp_prerequisites.py` validating ADC presence, project quota access, and Cloud SQL proxy port.
  - Document workstation onboarding commands in `docs/runbooks/gcp-local-demo.md`.

---

### PR 2 — Knowledge Retrieval, Enterprise Persistence & Evaluation (spec-0049) — [x] DELIVERED

#### Milestone B0 — Vertex AI Embeddings & Vector Dimension Isolation — [x] Completed
- **Failing test first:** `tests/rag/test_vertex_dimension_isolation.py` asserting dimension mismatch error on mixed Chroma collections.
- **Depends on:** Milestone A0.
- **Deliverables:**
  - Document isolated collection conventions (`mangomas_vertex`, `./data/chroma_vertex`).
  - Verify `mangomas rag ingest` and `mangomas rag query` against `text-embedding-004`.

#### Milestone B1 — Cloud SQL Postgres Persistence via Auth Proxy — [x] Completed
- **Failing test first:** `tests/postgres/test_postgres_history_contract.py` asserting JSON line parity between `PostgresRepository` and `SQLiteRepository`.
- **Depends on:** Milestone A1.
- **Deliverables:**
  - Document Cloud SQL Auth Proxy startup procedure.
  - Verify `mangomas chat` turn persistence and `mangomas history` row retrieval.

#### Milestone B2 — Automated Vertex AI Evaluation Harness & CI Quality Gate — [x] Completed
- **Failing test first:** `tests/eval/test_eval_gate_falsifiability.py` proving exit code 3 on score drop under Vertex AI.
- **Depends on:** Milestone A0.
- **Deliverables:**
  - Execute `mangomas eval` using `llm_judge` and `embedding` scorers against Vertex AI.
  - Verify automated exit code 3 termination on gate regression.

---

### PR 3 — Observability, Workflows & Cloud Run Verification (spec-0049) — [x] DELIVERED

#### Milestone C0 — Distributed Cloud Trace Export & JSON Logging — [x] Completed
- **Failing test first:** `tests/test_telemetry_gcp_contract.py` asserting Cloud Logging JSON schema and trace context headers.
- **Depends on:** Milestone A0.
- **Deliverables:**
  - Verify CLI span export to Google Cloud Trace under `MANGOMAS_TELEMETRY__EXPORTER=gcp`.
  - Verify Cloud Logging JSON output format with `severity`, `trace_id`, and `span_id`.

#### Milestone C1 — Declarative Workflow Graphs & Cloud Run Manifest Audit — [x] Completed
- **Failing test first:** `tests/deploy/test_manifest_render_contract.py` asserting zero unrendered placeholders in dev overlay, and `tests/agents/test_vertex_tool_agent.py` for ToolAgent multi-step reasoning and declarative workflows under Vertex AI.
- **Depends on:** Milestone B0, B1.
- **Deliverables:**
  - Execute multi-agent declarative workflows (`mangomas workflow run`) on Vertex AI.
  - Validate and render Cloud Run manifests via `scripts/deploy_environment.py`.

#### Milestone C2 — Operational Runbook Completion & Plan/Spec Reconciliation — [x] Completed
- **Failing test first:** Verify zero unrendered placeholders, full test suite passing, and complete runbook documentation.
- **Depends on:** Milestone C0, C1.
- **Deliverables:**
  - Complete `docs/runbooks/gcp-local-demo.md` with Story 2.2 (`ToolAgent` multi-step reasoning and declarative workflows) and Journey 5 (Cloud Telemetry verification and Cloud Run manifest validation) in dual PowerShell and Bash syntax.
  - Reconcile `specs/0049-gcp-integration-demonstration.md` and `docs/plans/20261109T010000Z-gcp-integration-demonstration-plan.md` marking offline acceptance criteria and milestones complete while tracking live billed-project items open in `specs/0049-gcp-integration-demonstration.md`.

---

## 5. Deferred / Out of Scope

1. **In-Cluster Kubernetes / GKE Deployment**:
   - Out of scope per ADR-0004 and Spec-0004. Cloud Run remains the exclusive serverless compute target for Mango-Mas.
2. **Direct Cross-Region Model Routing**:
   - Vertex AI requests are dispatched to a single configured region (`MANGOMAS_LLM__LOCATION=us-central1`). Dynamic multi-region latency-based failover is deferred to future enterprise specifications.
3. **Automated Live Cloud Infrastructure Provisioning (Terraform / Pulumi)**:
   - Out of scope per Requirement R4 (Non-Destructive Planning Guardrail). Demonstration focuses on local CLI execution against existing or proxied cloud resources.

---

## 6. Verification

```bash
# 1. Full Pre-PR Gate (Lint, Format, Typecheck, Import Linter, Tests)
make gate

# 2. Verify Configuration & Environment Schema Contracts
python -m pytest tests/deploy/test_env_example_contract.py tests/deploy/test_manifest_provider_contract.py -q

# 3. Verify CLI Surface & Exit Codes
python -m pytest tests/test_cli_surface.py tests/test_cli.py tests/test_cli_runtime.py -q

# 4. Gated Live Verification against GCP Services (when ADC and Project are configured)
RUN_VERTEX=1 VERTEX_PROJECT_ID="<YOUR_GCP_PROJECT_ID>" python -m pytest tests/vertex/test_smoke.py tests/vertex/test_embeddings.py -q --no-cov
RUN_GCP_SECRETS=1 GCP_SECRETS_PROJECT="<YOUR_GCP_PROJECT_ID>" python -m pytest tests/integration/test_gcp_secrets_live.py -q --no-cov
RUN_GCP_TRACE=1 python -m pytest tests/test_telemetry.py tests/test_telemetry_gcp_contract.py -m gcp_trace -q --no-cov
```
