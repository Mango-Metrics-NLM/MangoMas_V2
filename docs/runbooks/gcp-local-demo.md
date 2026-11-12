# Runbook: Google Cloud Platform (GCP) Local CLI Demonstration

This runbook guides operators and developers through demonstrating and validating
Mango-Mas capabilities running against Google Cloud Platform (GCP) services from a
local workstation CLI, achieving functional parity with local LM Studio workflows.

All commands provide dual syntax for **PowerShell** (Windows) and **Bash** (macOS/Linux).

Specification: [spec-0049](../../specs/0049-gcp-integration-demonstration.md).
Configuration template: [`.env.gcp-demo`](../../.env.gcp-demo).

## 0. Five-Minute Quickstart

```bash
pip install -e ".[dev,cloud,rag]"            # cloud = vertex + postgres + gcp extras
gcloud auth application-default login
make gcp-preflight                           # ADC, project, billing, APIs, Postgres endpoint
mangomas agents                              # registry boots against the configured provider
mangomas chat "Say hello from Gemini" -a chat
mangomas chat "Plan a 2-step health check" -a planner
```

Load the template first: copy the `MANGOMAS_*` lines from `.env.gcp-demo` into
`.env`, or export them, and set your project ID. If `make gcp-preflight` reports
`[FAIL] billing`, stop there: every Vertex call will be rejected with
`PERMISSION_DENIED` until the billing account is open. The pre-flight exists
because a project can show billing *enabled* while its billing account is
*closed*.

---

## 1. Workstation Setup & Ambient IAM Prerequisites

### 1.1 Google Cloud SDK & Ambient Application Default Credentials (ADC)
Mango-Mas authenticates with Google Cloud services using ambient Application Default
Credentials (ADC) without requiring static service account keys on developer machines.

1. **Log in to gcloud CLI**:
   ```bash
   gcloud auth login
   ```
2. **Generate local Application Default Credentials**:
   ```bash
   gcloud auth application-default login
   ```
3. **Configure default project**:
   ```bash
   gcloud config set project <YOUR_GCP_PROJECT_ID>
   ```
4. **Verify ADC token issuance** (stdout is discarded, so no part of the bearer token
   reaches the terminal, a recording, or a captured transcript):
   **Bash**:
   ```bash
   gcloud auth application-default print-access-token >/dev/null && echo "ADC token issued (not printed)"
   ```
   **PowerShell**:
   ```powershell
   gcloud auth application-default print-access-token | Out-Null; if ($?) { "ADC token issued (not printed)" }
   ```

### 1.2 Required IAM Roles
The authenticated Google Cloud user or service account requires the following roles:
- `roles/aiplatform.user` (Vertex AI LLM & text embeddings)
- `roles/secretmanager.secretAccessor` (GCP Secret Manager resolution)
- `roles/serviceusage.serviceUsageConsumer` (API quota consumption)
- `roles/cloudtrace.agent` (OpenTelemetry Cloud Trace export)
- `roles/cloudsql.client` (Cloud SQL Auth Proxy connection)

### 1.3 Service API Enablement
Enable the required Google Cloud APIs for your target project:
```bash
gcloud services enable \
  aiplatform.googleapis.com \
  secretmanager.googleapis.com \
  cloudtrace.googleapis.com \
  sqladmin.googleapis.com \
  --project="<YOUR_GCP_PROJECT_ID>"
```

### 1.4 Local Environment Installation
Install the required dependencies including cloud and RAG extras:

**PowerShell (Windows)**:
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev,cloud,rag]"
pip install -e ./mango-integration-contracts
```

**Bash (Linux/macOS)**:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,cloud,rag]"
pip install -e ./mango-integration-contracts
```

### 1.5 Local Pre-Flight Verification
`scripts/verify_gcp_prerequisites.py` runs five read-only checks. Its defaults come from the same
`MANGOMAS_*` settings the CLI reads, so the script and the app cannot disagree:

| Check | Passes when | Typical FAIL remediation |
|---|---|---|
| `adc_credentials` | ADC exists and its token refreshes within `--timeout` | `gcloud auth application-default login` |
| `gcp_project` | `--project`, `MANGOMAS_LLM__PROJECT_ID` / `EMBEDDINGS__` / `SECRETS__`, SDK env vars, or ADC names a project, and every project a provider targets is a valid ID | `gcloud config set project <ID>` |
| `billing` | Billing is enabled **and** the linked billing account is open, in every project a provider targets | Reopen or link a billing account |
| `required_apis` | Every API implied by the configured providers is enabled (`aiplatform` for a Vertex LLM, a Vertex `MANGOMAS_LLM_PROFILES__*` profile or Vertex embeddings; `secretmanager` for GCP secrets and any cross-project `projects/<id>/secrets/...` ref; `cloudtrace` for the GCP trace exporter or when `MANGOMAS_HARNESS__ENABLED=true` with `METRICS_EXPORTER=gcp`; `monitoring` when `MANGOMAS_TELEMETRY__METRICS_ENABLED=true` with `EXPORTER=gcp`), plus any `--require-api`, each in the project its client targets | `gcloud services enable <api> --project <ID>` (one line per project) |
| `postgres_endpoint` | The host/port in `MANGOMAS_DB__URL` accepts TCP (only when `MANGOMAS_DB__PROVIDER=postgres`) | Start `cloud-sql-proxy`, or use SQLite; percent-encode reserved characters in the password |

A 403 on the billing or Service Usage probe is reported as **SKIP** (the state could not be verified),
not FAIL. A TCP pass proves something listens on the port: the Auth Proxy or a local Postgres.

Each provider is checked in the project its runtime client uses: its own `*_PROJECT_ID`, else
(for a named LLM profile) `MANGOMAS_LLM__PROJECT_ID`, else where `google.auth.default()` points
(`GOOGLE_CLOUD_PROJECT`, `GCLOUD_PROJECT`, then the ADC project). `--project` sets the primary
project and the project for `--require-api`; nothing at runtime reads it, so it does not move a
project-less client. With Vertex in one project and Secret Manager (or a cross-project
`projects/<id>/secrets/...` reference) in another, the `billing` and `required_apis` rows report
each project separately, prefixed `[project-id]`. One more rule mirrors the app:
`MANGOMAS_SECRETS__PROVIDER=gcp` without `MANGOMAS_SECRETS__PROJECT_ID` fails `gcp_project` (the
app refuses to start).

`MANGOMAS_DB__URL` must use `postgresql://` or `postgres://` (the schemes asyncpg accepts) and
must percent-encode reserved characters in the password (`@` → `%40`, `:` in an unauthenticated
authority, `/` → `%2F`, `?` → `%3F`, `#` → `%23`), as libpq and asyncpg require. Bracketed IPv6
hosts (`[::1]:5432`) are validated via `ipaddress.IPv6Address`. These URLs fail without being
echoed:

- **Ambiguous:**
  - more than one `@` in the host part (asyncpg splits at the first `@`, RFC readers at the last);
  - an `@` after the host part;
  - an unbracketed multi-colon authority without `@host` (such as `postgresql://user:pass:5432/db`,
    rejected without DNS lookup so a mistyped password is never sent to DNS);
  - an all-digit host.
- **Not probed:** forms asyncpg resolves differently from a single TCP probe:
  - a multi-host list;
  - a percent-encoded host;
  - a `?host=`, `?port=`, or `?service=` override.
- **Invalid host/port:** an unbracketed IPv6 literal, stray `[`/`]`, or a port that isn't plain
  ASCII digits in `1..65535`.

A URL with no `user@` part is still probed, but its host and port are withheld from the report.
`postgresql://user:12345/db` (a forgotten `@host`) cannot be told apart from
`postgresql://host:12345/db`.

**PowerShell**:
```powershell
make gcp-preflight
# or directly, with options:
python scripts/verify_gcp_prerequisites.py --project "<YOUR_GCP_PROJECT_ID>" --verbose
```

**Bash**:
```bash
make gcp-preflight GCP_PREFLIGHT_FLAGS="--project <YOUR_GCP_PROJECT_ID>"
```

**Emit JSON report (for CI or automated pipelines)**:
```bash
python scripts/verify_gcp_prerequisites.py --project "<YOUR_GCP_PROJECT_ID>" --json
```

Exit codes: `0` = all checks passed or skipped, `1` = at least one FAIL, `2` = invalid arguments.

---

## 2. Journey 1: Vertex AI Chat & Structured Output Validation

Journey 1 demonstrates executing conversational agents and structured reasoning agents
against Vertex AI foundation models (`gemini-2.5-flash`), with strict output validation.

### 2.1 Environment Configuration

**PowerShell (Windows)**:
```powershell
$env:MANGOMAS_ENV = "dev"
$env:MANGOMAS_LOG_LEVEL = "DEBUG"
$env:MANGOMAS_LOG__FORMAT = "json"
$env:MANGOMAS_LLM__PROVIDER = "vertex"
$env:MANGOMAS_LLM__PROJECT_ID = "<YOUR_GCP_PROJECT_ID>"
$env:MANGOMAS_LLM__LOCATION = "us-central1"
$env:MANGOMAS_LLM__MODEL = "gemini-2.5-flash"
$env:MANGOMAS_LLM__TEMPERATURE = "0.2"
```

**Bash (Linux/macOS)**:
```bash
export MANGOMAS_ENV="dev"
export MANGOMAS_LOG_LEVEL="DEBUG"
export MANGOMAS_LOG__FORMAT="json"
export MANGOMAS_LLM__PROVIDER="vertex"
export MANGOMAS_LLM__PROJECT_ID="<YOUR_GCP_PROJECT_ID>"
export MANGOMAS_LLM__LOCATION="us-central1"
export MANGOMAS_LLM__MODEL="gemini-2.5-flash"
export MANGOMAS_LLM__TEMPERATURE="0.2"
```

*Note*: Active GA models on Vertex AI include `gemini-2.5-flash` (fast, cost-effective multimodal)
and `gemini-2.5-pro` (complex multi-step reasoning). All model identifiers are environment-driven.

### 2.2 Execution Steps

#### Step 1: Verify Agent Registry
Confirm that all core agents (`chat`, `summarize`, `tool`, `planner`, `reviewer`) are registered:
```bash
mangomas agents
```

#### Step 2: Single-Turn Conversational Parity
Test basic conversational dispatch to Vertex AI Gemini:
```bash
mangomas chat "Explain the architectural difference between local LM Studio and Vertex AI in Mango-Mas in two concise sentences." -a chat
```

#### Step 3: Structured Output with Planner Agent (`ExecutionPlan`)
Enable strict output validation and prompt the `planner` agent to produce a structured JSON execution plan:

**PowerShell**:
```powershell
$env:MANGOMAS_AGENTS__PLANNER__VALIDATE_OUTPUT = "true"
mangomas chat "Create a 3-step migration plan to transition turn persistence from SQLite to Cloud SQL PostgreSQL." -a planner --verbose
```

**Bash**:
```bash
export MANGOMAS_AGENTS__PLANNER__VALIDATE_OUTPUT="true"
mangomas chat "Create a 3-step migration plan to transition turn persistence from SQLite to Cloud SQL PostgreSQL." -a planner --verbose
```

**Expected Result**:
The agent outputs a valid JSON object matching the `ExecutionPlan` Pydantic schema
(`src/mangomas/agents/planner.py`): a required `goal` string and a non-empty `steps` list
whose items carry a required `step` (integer ≥ 1) and `description`, plus an optional `agent`.
Any other shape, including a missing `goal` or a `step_id` key, fails validation and raises
`LLMBadResponse`:
```json
{
  "goal": "Transition turn persistence from SQLite to Cloud SQL PostgreSQL",
  "steps": [
    {
      "step": 1,
      "description": "Provision the Cloud SQL Postgres instance and start the local Auth Proxy",
      "agent": "tool"
    },
    {
      "step": 2,
      "description": "Set MANGOMAS_DB__PROVIDER=postgres and let the repository run its idempotent schema migration",
      "agent": "tool"
    },
    {
      "step": 3,
      "description": "Run the history-parity suite against the PostgreSQL turn repository",
      "agent": "reviewer"
    }
  ]
}
```

#### Step 4: Structured Output with Reviewer Agent (`ReviewResult`)
Enable strict validation on the `reviewer` agent:

**PowerShell**:
```powershell
$env:MANGOMAS_AGENTS__REVIEWER__VALIDATE_OUTPUT = "true"
mangomas chat "Review proposal: Disable database transaction logging to speed up chat completions." -a reviewer --verbose
```

**Bash**:
```bash
export MANGOMAS_AGENTS__REVIEWER__VALIDATE_OUTPUT="true"
mangomas chat "Review proposal: Disable database transaction logging to speed up chat completions." -a reviewer --verbose
```

**Expected Result**:
A valid JSON object matching the `ReviewResult` schema (`src/mangomas/agents/reviewer.py`):
a required boolean `passed`, a `score` in `[0, 1]`, a `feedback` string, and an optional
`suggestions` list. There is no `verdict` field; the workflow predicate in §7.4 reads `passed`:
```json
{
  "passed": false,
  "score": 0.1,
  "feedback": "Disabling transaction logging compromises ACID compliance, data durability, and disaster recovery capabilities.",
  "suggestions": [
    "Profile the completion path before touching durability settings",
    "Batch turn writes instead of disabling the write-ahead log"
  ]
}
```

### 2.3 Assertions & Negative Prompt Verification
- **Negative Prompt Compliance**: Gemini models adhere strictly to prompt instructions ("Do not include markdown fences"). When `VALIDATE_OUTPUT=true`, responses wrapped in ` ```json ` blocks fail `model_validate_json` and raise `LLMBadResponse` (exit code 1).
- **Fallback Verification**: If ADC is unconfigured or network is unavailable, switch back to local LM Studio without modifying code:
  - PowerShell: `$env:MANGOMAS_LLM__PROVIDER = "lmstudio"`
  - Bash: `export MANGOMAS_LLM__PROVIDER="lmstudio"`

---

## 3. Journey 2: Knowledge Retrieval with Vertex AI Embeddings & Isolated Chroma Store (Story 3.1)

Journey 2 demonstrates ingesting unstructured documentation and performing semantic retrieval
using Google Cloud Vertex AI text embeddings (`text-embedding-004`), with strict vector dimension
isolation in Chroma.

### 3.1 Environment Configuration

**PowerShell (Windows)**:
```powershell
$env:MANGOMAS_EMBEDDINGS__ENABLED = "true"
$env:MANGOMAS_EMBEDDINGS__PROVIDER = "vertex"
$env:MANGOMAS_EMBEDDINGS__PROJECT_ID = "<YOUR_GCP_PROJECT_ID>"
$env:MANGOMAS_EMBEDDINGS__LOCATION = "us-central1"
$env:MANGOMAS_EMBEDDINGS__MODEL = "text-embedding-004"
$env:MANGOMAS_EMBEDDINGS__BATCH_SIZE = "32"

# CRITICAL: Isolate vector store collection and directory for 768-dim embeddings
$env:MANGOMAS_VECTOR__ENABLED = "true"
$env:MANGOMAS_VECTOR__PROVIDER = "chroma"
$env:MANGOMAS_VECTOR__PERSIST_DIR = "./data/chroma_vertex"
$env:MANGOMAS_VECTOR__COLLECTION = "mangomas_vertex"
$env:MANGOMAS_VECTOR__TOP_K = "5"
$env:MANGOMAS_VECTOR__MAX_TOP_K = "50"
```

**Bash (Linux/macOS)**:
```bash
export MANGOMAS_EMBEDDINGS__ENABLED="true"
export MANGOMAS_EMBEDDINGS__PROVIDER="vertex"
export MANGOMAS_EMBEDDINGS__PROJECT_ID="<YOUR_GCP_PROJECT_ID>"
export MANGOMAS_EMBEDDINGS__LOCATION="us-central1"
export MANGOMAS_EMBEDDINGS__MODEL="text-embedding-004"
export MANGOMAS_EMBEDDINGS__BATCH_SIZE="32"

# CRITICAL: Isolate vector store collection and directory for 768-dim embeddings
export MANGOMAS_VECTOR__ENABLED="true"
export MANGOMAS_VECTOR__PROVIDER="chroma"
export MANGOMAS_VECTOR__PERSIST_DIR="./data/chroma_vertex"
export MANGOMAS_VECTOR__COLLECTION="mangomas_vertex"
export MANGOMAS_VECTOR__TOP_K="5"
export MANGOMAS_VECTOR__MAX_TOP_K="50"
```

### 3.2 Dimension Isolation & Embedding Model Guidance
- **Dimension Isolation**: Local models (such as `all-MiniLM-L6-v2`) generate 384-dimensional vectors stored in `./data/chroma` (`mangomas`). Vertex AI `text-embedding-004` generates 768-dimensional dense vectors. In Chroma, collection vector space dimensionality is fixed upon collection initialization. Attempting to write or query 768-dimensional vectors against an existing 384-dimensional collection raises a typed `PersistenceError` (wrapping Chroma's `InvalidArgumentError`). Configuring isolated paths (`./data/chroma_vertex` and `mangomas_vertex`) allows local and cloud embeddings to coexist without collision.
- **Dynamic Model Selection**: Model selection is 100% Settings/env-driven via `MANGOMAS_EMBEDDINGS__MODEL`. `text-embedding-004` returns 768 dimensions. `gemini-embedding-001` returns **3072 by default**, and the adapter does not pass `output_dimensionality` today. So switching models changes the vector size: give each embedding model its own collection and persist directory, then re-ingest.

### 3.3 Execution Steps

#### Step 1: Ingest Local Documentation via RAG Pipeline
Ingest repository documentation into the isolated Chroma vector store:
```bash
mangomas rag ingest README.md
```
**Expected Output**:
```text
ingested docs=1 chunks=4 batches=1 deleted_sources=0
```

#### Step 2: Query Knowledge Base with Vertex Embeddings
Perform semantic vector search using cosine similarity:
```bash
mangomas rag query "Explain multi-agent architecture" --top-k 3
```
**Expected Output**:
Ranked passages with cosine similarity scores normalized to `[0.0, 1.0]`:
```text
[1] score=0.884 source=README.md
# Mango-Mas V2 — Agent Context
Local-first, modular agent platform built on FastAPI + LM Studio...
```

---

## 4. Journey 3: State Persistence with Cloud SQL Postgres & Auth Proxy (Story 4.1)

Journey 3 demonstrates persisting agent turns into a managed Google Cloud SQL PostgreSQL database
from the local workstation via the Cloud SQL Auth Proxy, achieving 100% data shape and multi-tenancy
parity with local SQLite.

### 4.1 Cloud SQL Auth Proxy Setup

The Cloud SQL Auth Proxy provides secure, encrypted mTLS access to your Cloud SQL instance without
opening public IP firewall rules. It authenticates using your local ambient Application Default
Credentials (`roles/cloudsql.client`).

#### Step 1: Install Cloud SQL Auth Proxy (v2)

**PowerShell (Windows)**:
```powershell
Invoke-WebRequest -Uri "https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.14.2/cloud-sql-proxy.x64.exe" -OutFile "cloud-sql-proxy.exe"
```

**Bash (Linux/macOS)**:
```bash
curl -o cloud-sql-proxy "https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.14.2/cloud-sql-proxy.linux.amd64"
chmod +x cloud-sql-proxy
```

#### Step 2: Start Auth Proxy in a Background Terminal

Replace `<PROJECT_ID>`, `<REGION>`, and `<INSTANCE_NAME>` with your Cloud SQL instance connection name:

**PowerShell**:
```powershell
.\cloud-sql-proxy.exe <PROJECT_ID>:<REGION>:<INSTANCE_NAME> --port 5432
```

**Bash**:
```bash
./cloud-sql-proxy <PROJECT_ID>:<REGION>:<INSTANCE_NAME> --port 5432
```

*Note*: If local port 5432 is already bound by a local service, specify `--port 5433` and update `MANGOMAS_DB__URL` accordingly.

#### Step 3: Verify Proxy Reachability

Run the pre-flight verification script to confirm socket reachability:
```bash
python scripts/verify_gcp_prerequisites.py --project "<YOUR_GCP_PROJECT_ID>" --require-api sqladmin.googleapis.com
```

### 4.2 Database Configuration

**PowerShell (Windows)**:
```powershell
$env:MANGOMAS_DB__PROVIDER = "postgres"
$env:MANGOMAS_DB__URL = "postgresql://mangomas:<YOUR_DB_PASSWORD>@127.0.0.1:5432/mangomas"
$env:MANGOMAS_DB__POOL_MIN = "1"
$env:MANGOMAS_DB__POOL_MAX = "10"
$env:MANGOMAS_DB__CONNECT_TIMEOUT_SECONDS = "10.0"
```

**Bash (Linux/macOS)**:
```bash
export MANGOMAS_DB__PROVIDER="postgres"
export MANGOMAS_DB__URL="postgresql://mangomas:<YOUR_DB_PASSWORD>@127.0.0.1:5432/mangomas"
export MANGOMAS_DB__POOL_MIN="1"
export MANGOMAS_DB__POOL_MAX="10"
export MANGOMAS_DB__CONNECT_TIMEOUT_SECONDS="10.0"
```

### 4.3 Execution & Persistence Verification

#### Step 1: Execute Turn Persisted to Cloud SQL
Execute a conversation turn against Vertex AI or local model. On the first turn, `PostgresRepository`
automatically and idempotently creates the `turns` table, applies tenant and record migrations,
and registers `jsonb` type codecs:
```bash
mangomas chat "Persist this conversation turn directly to Google Cloud SQL PostgreSQL." -a chat
```

#### Step 2: Inspect Persisted Turns via CLI History
Inspect recent turns using `mangomas history`:
```bash
mangomas history --limit 3
```

**Expected JSON Lines Output**:
Each row outputs as a JSON line with exact parity to SQLite output:
```json
{"id": 1, "ts": "2026-11-09T03:15:00.123456+00:00", "agent": "chat", "request": {"messages": [{"role": "user", "content": "Persist this conversation turn directly to Google Cloud SQL PostgreSQL."}]}, "response": {"content": "Turn successfully stored in Cloud SQL Postgres.", "agent": "chat"}, "schema_version": 2, "status": "ok", "error_code": null, "error": null}
```

#### Step 3: Multi-Tenancy Scoping Verification
In shared environments with multi-tenancy enabled (`MANGOMAS_TENANCY__ENABLED=true`), turns are
partitioned by `X-Tenant-ID`. Queries under `mangomas history` only return turns for the active tenant,
guaranteeing complete tenant isolation across both SQLite and Cloud SQL.

---

## 5. Journey 4: Continuous Evaluation Harness against Vertex AI & CI Quality Gate Falsifiability (Story 5.1)

Journey 4 demonstrates deterministic quality gating, LLM-as-a-judge scoring with Vertex AI Gemini,
semantic embedding similarity scoring with Vertex AI `text-embedding-004`, and CI quality gate
falsifiability enforcement (exit code 3 `EVAL_GATE_EXIT_CODE`).

### 5.1 Environment Configuration for Evaluation

**PowerShell (Windows)**:
```powershell
# Target Vertex AI for LLM evaluation (Gemini 2.5 Flash)
$env:MANGOMAS_LLM__PROVIDER = "vertex"
$env:MANGOMAS_LLM__PROJECT_ID = "<YOUR_GCP_PROJECT_ID>"
$env:MANGOMAS_LLM__LOCATION = "us-central1"
$env:MANGOMAS_LLM__MODEL = "gemini-2.5-flash"

# Enable Vertex AI Embeddings for semantic scoring
$env:MANGOMAS_EMBEDDINGS__ENABLED = "true"
$env:MANGOMAS_EMBEDDINGS__PROVIDER = "vertex"
$env:MANGOMAS_EMBEDDINGS__PROJECT_ID = "<YOUR_GCP_PROJECT_ID>"
$env:MANGOMAS_EMBEDDINGS__LOCATION = "us-central1"
$env:MANGOMAS_EMBEDDINGS__MODEL = "text-embedding-004"
```

**Bash (Linux/macOS)**:
```bash
export MANGOMAS_LLM__PROVIDER="vertex"
export MANGOMAS_LLM__PROJECT_ID="<YOUR_GCP_PROJECT_ID>"
export MANGOMAS_LLM__LOCATION="us-central1"
export MANGOMAS_LLM__MODEL="gemini-2.5-flash"

export MANGOMAS_EMBEDDINGS__ENABLED="true"
export MANGOMAS_EMBEDDINGS__PROVIDER="vertex"
export MANGOMAS_EMBEDDINGS__PROJECT_ID="<YOUR_GCP_PROJECT_ID>"
export MANGOMAS_EMBEDDINGS__LOCATION="us-central1"
export MANGOMAS_EMBEDDINGS__MODEL="text-embedding-004"
```

### 5.2 Step 1: Automated LLM-as-a-Judge Evaluation with Gemini

> **Self-grading & calibration caveat.** `llm_judge` grades with the system-under-test's own LLM client,
> here the same Gemini model that produced the answer, unless `MANGOMAS_EVAL__JUDGE_PROFILE`
> delegates judging to a separate configured LLM profile (spec-0048). By default (with no
> `MANGOMAS_EVAL__JUDGE_CALIBRATION_PATH` record pinned to the active judge model and
> `prompt_sha256`), `llm_judge` scores are **reported, not gated** (`gate=PASS (note: llm_judge uncalibrated …)`,
> spec-0047), and the harness logs `eval_llm_judge_self_grading` once per run (spec-0045 R7).
> For an out-of-the-box hard CI gate without a calibration record, prefer deterministic scorers
> (`exact_match`, `json_field`, `contains`), semantic `embedding` similarity, or trunk's
> committed lanes (`make eval-lanes`), and quote the Wilson interval rather than a bare mean on
> a 2-row dataset.

Run the evaluation harness over a test dataset using `llm_judge`, writing a baseline JSON report:
```powershell
mangomas eval -d tests/eval/fixtures/all_pass.jsonl -s llm_judge -a chat --gate --min-mean-score 0.7 -o eval-output/vertex_eval_report.json
```
- **Console Output**:
  ```text
  scorer=llm_judge agent=chat size=2 passed=2 failed=0 errored=0 mean_score=0.950
    [PASS] p1 score=0.950
    [PASS] p2 score=0.950
  judge=llm_judge uncalibrated (no calibration record; prompt sha256:…; self-preference: not_measured)
  gate=PASS (note: llm_judge uncalibrated (no calibration record; prompt sha256:…; self-preference: not_measured) — score thresholds not enforced)
  Report written to eval-output/vertex_eval_report.json
  ```
- **Exit Code**: `0` ($LASTEXITCODE in PowerShell, $? in Bash).

### 5.3 Step 2: Semantic Embedding Similarity Evaluation
Score predictions against expected outputs via cosine similarity of Vertex `text-embedding-004` vectors:
```powershell
mangomas eval -d tests/eval/fixtures/all_pass.jsonl -s embedding -a chat -o eval-output/vertex_embedding_eval.json
```
- **Console Output**:
  ```text
  scorer=embedding agent=chat size=2 passed=2 failed=0 errored=0 mean_score=0.980
    [PASS] p1 score=0.980
    [PASS] p2 score=0.980
  ```

### 5.4 Step 3: Falsifiability Demonstration — CI Quality Gate Enforcement (Exit Code 3)
Falsifiability proves that defective performance or sub-threshold metrics reliably break the CI build:
```powershell
mangomas eval -d tests/eval/fixtures/mixed.jsonl -s exact_match -a chat --gate --min-mean-score 0.9 --output-json eval-output/failing_gate_report.json
```
- **Console Output**:
  ```text
  scorer=exact_match agent=chat size=3 passed=2 failed=1 errored=0 mean_score=0.667
    [PASS] m1 score=1.000
    [FAIL] m2 score=0.000
    [PASS] m3 score=1.000
  gate=FAIL (mean_score 0.667 < min_mean_score 0.900)
  Report written to eval-output/failing_gate_report.json
  ```
- **Exit Code**: `3` (`EVAL_GATE_EXIT_CODE`).
- **Artifact Preservation**: Inspect `eval-output/failing_gate_report.json`. Notice that even though the process halted with exit code 3, the complete evaluation report was written to disk before exit, allowing CI systems to publish report artifacts on failed builds.

### 5.5 Step 4: Regression Gate Enforcement against Saved Baseline
Prevent regressions across revisions by diffing against `vertex_eval_report.json`:
```powershell
mangomas eval -d tests/eval/fixtures/mixed.jsonl -s exact_match -a chat --gate --baseline eval-output/vertex_eval_report.json --max-mean-score-drop 0.1 --no-allow-new-failures
```
- **Console Output**:
  ```text
  gate=FAIL (mean_score dropped 0.283 > max 0.100; 1 row regression(s): m2)
  ```
- **Exit Code**: `3` (`EVAL_GATE_EXIT_CODE`).

---

## 6. Story 4.2: Strict GCP Secret Manager Resolution & Fail-Closed Auth

Mango-Mas integrates with Google Cloud Secret Manager via `GCPSecretManagerProvider`.
In production and development, strict mode (`MANGOMAS_SECRETS__STRICT=true`) ensures permission,
authentication, timeout, or API failures raise a typed `SecretsResolutionError` rather than
silently proceeding with degraded credentials (`NotFound` still returns `None` per ADR-002 / ADR-0010).

> **Provider credential contract (`MANGOMAS_LLM__SECRET_REF`).** Under `MANGOMAS_LLM__PROVIDER=vertex`,
> `_vertex_factory` (`src/mangomas/composition/llm.py`) forwards a resolved `MANGOMAS_LLM__SECRET_REF`
> as a **service-account JSON body** (`credentials_json`); ambient ADC requires leaving
> `MANGOMAS_LLM__SECRET_REF` unset. Plain string bearer tokens belong on `MANGOMAS_LLM__SECRET_REF`
> when `MANGOMAS_LLM__PROVIDER=lmstudio` (or on `MANGOMAS_AUTH__SECRET_REF` when running the HTTP API
> with `MANGOMAS_AUTH__ENABLED=true`).

### 6.1 Strict Mode Configuration

**PowerShell (Windows)**:
```powershell
$env:MANGOMAS_SECRETS__PROVIDER = "gcp"
$env:MANGOMAS_SECRETS__PROJECT_ID = "<YOUR_GCP_PROJECT_ID>"
$env:MANGOMAS_SECRETS__STRICT = "true"
$env:MANGOMAS_SECRETS__DEFAULT_VERSION = "latest"
# Under PROVIDER=vertex, SECRET_REF must hold a service-account JSON payload (or test an unauthorized secret ref):
$env:MANGOMAS_LLM__SECRET_REF = "mangomas-vertex-sa-json"
```

**Bash (Linux/macOS)**:
```bash
export MANGOMAS_SECRETS__PROVIDER="gcp"
export MANGOMAS_SECRETS__PROJECT_ID="<YOUR_GCP_PROJECT_ID>"
export MANGOMAS_SECRETS__STRICT="true"
export MANGOMAS_SECRETS__DEFAULT_VERSION="latest"
export MANGOMAS_LLM__SECRET_REF="mangomas-vertex-sa-json"
```

### 6.2 Fail-Closed Verification
1. Attempt to invoke the CLI referencing a secret to which the active principal lacks `roles/secretmanager.secretAccessor` (or without valid ADC):
   ```bash
   mangomas chat "Testing fail-closed secret resolution" -a chat
   ```
2. **Observed Behavior**:
   - The CLI halts immediately with configuration exit code 2 (`EXIT_CONFIG_ERROR`).
   - Stderr emits:
     ```text
     Configuration error: Failed to resolve secret 'mangomas-vertex-sa-json' from provider 'gcp'. (PermissionDenied)
     ```
   - **Log Redaction Guarantee**: Application logs never contain secret values, secret versions, or full GCP resource strings.
3. Clear `MANGOMAS_LLM__SECRET_REF` before returning to ambient ADC Vertex journeys:
   - **PowerShell**: `Remove-Item env:MANGOMAS_LLM__SECRET_REF -ErrorAction SilentlyContinue`
   - **Bash**: `unset MANGOMAS_LLM__SECRET_REF`

### 6.3 Non-Strict Fallback (Local Prototyping)
When strict mode is disabled (`MANGOMAS_SECRETS__STRICT=false`) under `MANGOMAS_LLM__PROVIDER=lmstudio`, unresolved cloud secrets return `None` and fall back to inline local settings (`MANGOMAS_LLM__API_KEY`):

**PowerShell**:
```powershell
$env:MANGOMAS_LLM__PROVIDER = "lmstudio"
$env:MANGOMAS_SECRETS__STRICT = "false"
$env:MANGOMAS_LLM__SECRET_REF = "mangomas-llm-api-key"
$env:MANGOMAS_LLM__API_KEY = "local-dev-fallback-key"
```

**Bash**:
```bash
export MANGOMAS_LLM__PROVIDER="lmstudio"
export MANGOMAS_SECRETS__STRICT="false"
export MANGOMAS_LLM__SECRET_REF="mangomas-llm-api-key"
export MANGOMAS_LLM__API_KEY="local-dev-fallback-key"
```

After testing the local fallback, clear both overrides before switching back to `vertex` in §7:
- **PowerShell**: `Remove-Item env:MANGOMAS_LLM__SECRET_REF, env:MANGOMAS_LLM__API_KEY -ErrorAction SilentlyContinue`
- **Bash**: `unset MANGOMAS_LLM__SECRET_REF MANGOMAS_LLM__API_KEY`

---

## 7. Story 2.2: ToolAgent Multi-Step Reasoning & Declarative Workflows

Story 2.2 demonstrates executing multi-step agent reasoning chains via `ToolAgent` and composing
multi-agent execution topologies using declarative workflow graphs, fully backed by Google Cloud
Vertex AI foundation models (`gemini-2.5-flash`).

### 7.1 Architecture & Role Folding Context

1. **ToolAgent Multi-Step Reasoning Loop (`src/mangomas/agents/tool_agent.py`)**:
   - `ToolAgent` acts as an autonomous execution coordinator. When prompted with a request, it exposes available tools from `ToolRegistry` (`ctx.tools`) in the LLM prompt.
   - If the model emits a tool call enclosed in a markdown code block (````json {"tool": "<name>", "arguments": {...}} ````), `ToolAgent` parses the call via `ToolCallParser`, invokes the target tool, and re-injects both the assistant's invocation (`Message(role="assistant")`) and the tool's result (`Message(role="tool", content=...)`) back into the conversation context.
   - The loop continues until the model produces a final answer without requesting further tools, or until the execution budget is exhausted.
   - The total number of LLM completions consumed is recorded in `AgentResponse.metadata["tool_steps"]`.

2. **Vertex AI Role Folding & Gemini Turn Parity (`src/mangomas/adapters/llm/vertex/_messages.py`)**:
   - Google Cloud Vertex AI Gemini models enforce a strict alternating turn structure (`user` -> `model` -> `user` -> `model`) and explicitly reject arbitrary role identifiers like `"tool"` or `"system"`.
   - `VertexClient` transparently folds message roles:
     - `_vertex_role_for`: Maps `"assistant"` to Gemini's native `"model"` role, and maps all other roles (`"user"`, `"system"`, `"tool"`) to `"user"`.
     - `_annotate`: Prefixes non-standard messages with role tags. A tool execution result `Message(role="tool", content='{"tool": "...", "output": ...}')` is automatically folded into a Gemini `user` turn prefixed as `[tool] {"tool": "...", "output": ...}`.
   - This provides seamless multi-step tool-calling compatibility on Vertex AI without modifying any agent interfaces or prompts.

3. **Step Budget Capping & Safety Halting**:
   - The total number of LLM calls in a single execution is strictly capped by `MANGOMAS_AGENTS__TOOL__MAX_TOOL_STEPS` (default `5`, `DEFAULT_TOOL_MAX_STEPS`).
   - If the model requests a tool call on step $N == \text{max\_tool\_steps}$, `ToolAgent` logs an informational notice (`"LLM-call budget exhausted; returning last response without executing tool"`), halts immediately without executing the tool, and returns the accumulated response with `metadata["tool_steps"] = max_tool_steps`.
   - This prevents runaway infinite loops and unexpected cloud token consumption.

4. **Declarative Workflow Graphs (`src/mangomas/workflow/`)**:
   - Enables authoring multi-agent execution pipelines as declarative, portable JSON graph definitions.
   - Supported node primitives:
     - `sequence`: Sequential execution chaining outputs across steps.
     - `fan_out`: Parallel concurrent execution across multiple agent branches with join reduction (e.g., `concat`).
     - `loop`: Iterative execution repeating an agent until an acceptance predicate (`contains`, `regex`, `json_field`) evaluates to true or `max_steps` is reached.
     - `branch`: Conditional routing based on predicate evaluation.
     - `agent`: Direct execution of a registered agent.
   - Offline validation (`mangomas workflow validate`) verifies graph syntax, schemas, and predicate specifications without invoking models.
   - Execution (`mangomas workflow run`) orchestrates the pipeline against the configured orchestrator and Vertex AI foundation models.

### 7.2 Environment Configuration

**PowerShell (Windows)**:
```powershell
$env:MANGOMAS_ENV = "dev"
$env:MANGOMAS_LOG_LEVEL = "INFO"
$env:MANGOMAS_LLM__PROVIDER = "vertex"
$env:MANGOMAS_LLM__PROJECT_ID = "<YOUR_GCP_PROJECT_ID>"
$env:MANGOMAS_LLM__LOCATION = "us-central1"
$env:MANGOMAS_LLM__MODEL = "gemini-2.5-flash"
$env:MANGOMAS_AGENTS__TOOL__MAX_TOOL_STEPS = "5"
```

**Bash (Linux/macOS)**:
```bash
export MANGOMAS_ENV="dev"
export MANGOMAS_LOG_LEVEL="INFO"
export MANGOMAS_LLM__PROVIDER="vertex"
export MANGOMAS_LLM__PROJECT_ID="<YOUR_GCP_PROJECT_ID>"
export MANGOMAS_LLM__LOCATION="us-central1"
export MANGOMAS_LLM__MODEL="gemini-2.5-flash"
export MANGOMAS_AGENTS__TOOL__MAX_TOOL_STEPS="5"
```

### 7.3 Step 1: Interactive Multi-Step Tool Reasoning

Execute an interactive multi-step reasoning task using `ToolAgent` targeting Vertex AI.

> **Prerequisite: the RAG tool.** The CLI composition root registers exactly one tool,
> `retrieve` (`RetrievalTool`, `src/mangomas/composition/rag.py`), and only when **both**
> `MANGOMAS_EMBEDDINGS__ENABLED=true` and `MANGOMAS_VECTOR__ENABLED=true`. Run this step in
> the shell you used for Journey 2 (§3), after `mangomas rag ingest README.md`. Without
> those settings `ctx.tools` is empty and the agent answers in one turn with no tool call.
> There is no `list_agents` tool in the CLI composition; a model that invents one gets
> `ToolNotFound`.

**PowerShell**:
```powershell
mangomas chat "Using the knowledge base, explain how Mango-Mas swaps local providers for GCP ones." -a tool
```

**Bash**:
```bash
mangomas chat "Using the knowledge base, explain how Mango-Mas swaps local providers for GCP ones." -a tool
```

#### Execution Trace & Lifecycle Verification:
1. **Turn 1 (Prompt & Model Tool Call)**:
   - `ToolAgent` prompts Vertex AI Gemini with system context and the registered `retrieve` tool spec
     (`{"query": string, "top_k"?: integer}`).
   - Gemini recognizes it needs grounded context and emits a structured JSON tool call:
     ```json
     {"tool": "retrieve", "arguments": {"query": "provider swap local to GCP", "top_k": 3}}
     ```
2. **Turn 2 (Tool Execution & Vertex Role Folding)**:
   - `ToolAgent` parses the tool call and invokes `retrieve` via `ToolRegistry`. The tool embeds the query
     with Vertex `text-embedding-004`, searches the isolated Chroma collection, and returns a ranked block:
     `[1] (score=0.812, source=README.md) ...`.
   - `ToolAgent` appends the assistant's call and the tool's result to conversation history:
     `Message(role="tool", content='{"tool": "retrieve", "output": "[1] (score=0.812, source=README.md) ..."}')`.
   - `VertexClient` translates this message into a Gemini-compatible `user` turn prefixed with `[tool]`:
     `[tool] {"tool": "retrieve", "output": "[1] (score=0.812, source=README.md) ..."}`.
3. **Turn 3 (Final Answer Generation)**:
   - Vertex AI Gemini receives the folded context and answers from the retrieved passages, citing
     `README.md` (exact wording varies by model and corpus).
   - `response.metadata["tool_steps"]` equals `2` (two LLM completions), and
     `response.metadata["tool_calls"]` records the one `retrieve` call.

### 7.4 Step 2: Declarative Workflow Validation (`mangomas workflow validate`)

Validate a multi-agent declarative workflow graph offline without network or cloud API calls.

#### Example Composite Workflow Definition:
Save the following graph to `workflows/gcp-analysis-pipeline.json` (or pass inline JSON). It defines a sequential pipeline that condenses input with `summarize`, fans out in parallel to `chat` and `planner` with `concat` join, and enters an iterative verification `loop` with `reviewer` until its `ReviewResult.passed` field is `true` (`json_field` predicate):

```json
{
  "schema_version": 1,
  "name": "gcp-analysis-pipeline",
  "root": {
    "kind": "sequence",
    "steps": [
      {
        "kind": "agent",
        "agent": "summarize"
      },
      {
        "kind": "fan_out",
        "join": "concat",
        "branches": [
          {"kind": "agent", "agent": "chat"},
          {"kind": "agent", "agent": "planner"}
        ]
      },
      {
        "kind": "loop",
        "agent": "reviewer",
        "max_steps": 3,
        "accept": {
          "kind": "json_field",
          "field": "passed",
          "equals": true
        }
      }
    ]
  }
}
```

#### Offline Validation Command:

**PowerShell**:
```powershell
mangomas workflow validate -f workflows/gcp-analysis-pipeline.json
```

**Bash**:
```bash
mangomas workflow validate -f workflows/gcp-analysis-pipeline.json
```

**Expected Output**:
```text
ok name=gcp-analysis-pipeline root=sequence
```
- **Exit Code**: `0` ($LASTEXITCODE in PowerShell, $? in Bash).

#### Falsifiability Proof (Invalid Syntax / Unknown Node Kind):
Verify that invalid workflow specifications fail loud with configuration exit code 2 (`EXIT_CONFIG_ERROR`):

**PowerShell**:
```powershell
mangomas workflow validate -f '{\"schema_version\": 1, \"name\": \"bad\", \"root\": {\"kind\": \"unsupported_node\"}}'
```

**Bash**:
```bash
mangomas workflow validate -f '{"schema_version": 1, "name": "bad", "root": {"kind": "unsupported_node"}}'
```

**Expected Output**:
```text
Workflow configuration error: ... Input tag 'unsupported_node' found using 'kind' does not match any of the expected tags ...
```
- **Exit Code**: `2` (`EXIT_CONFIG_ERROR`).

### 7.5 Step 3: Declarative Workflow Execution (`mangomas workflow run`)

Execute the composite multi-agent workflow against Vertex AI foundation models:

**PowerShell**:
```powershell
mangomas workflow run "Evaluate migrating turn persistence from local SQLite to Google Cloud SQL PostgreSQL" -f workflows/gcp-analysis-pipeline.json
```

**Bash**:
```bash
mangomas workflow run "Evaluate migrating turn persistence from local SQLite to Google Cloud SQL PostgreSQL" -f workflows/gcp-analysis-pipeline.json
```

**Workflow Execution Flow**:
1. `summarize`: Distills the migration request into key architectural requirements.
2. `fan_out`: Dispatches the condensed requirements to `chat` (for risk assessment) and `planner` (for structured step sequencing) concurrently, concatenating their outputs.
3. `loop`: Dispatches the concatenated analysis to `reviewer`, evaluating the output against the `json_field` predicate (terminating when `ReviewResult.passed` is `true`) up to 3 iterations.
4. Outputs the final accepted evaluation to stdout with exit code 0.

---

## 8. Journey 5: Cloud Telemetry Verification & Cloud Run Manifest Validation (Story 5.2)

Journey 5 demonstrates enterprise observability and production deployment verification: streaming
OpenTelemetry traces directly to Google Cloud Trace, formatting application logs to Google Cloud Logging
JSON schema with trace context injection, and performing offline validation and rendering of Cloud Run
deployment manifests with zero unrendered placeholders.

### 8.1 Architecture & Observability Context

1. **Google Cloud Trace Exporter (`src/mangomas/telemetry/exporters.py`)**:
   - When `MANGOMAS_TELEMETRY__EXPORTER=gcp` is configured, `_lazy_cloud_trace_exporter()` lazily loads `opentelemetry.exporter.cloud_trace.CloudTraceSpanExporter` from the `[gcp]` extra (included in `[cloud]`).
   - Spans generated across agent dispatch, LLM calls, and storage operations are captured and exported in batches to Google Cloud Trace (`cloudtrace.googleapis.com`) using ambient ADC (`roles/cloudtrace.agent`).
   - Standard W3C Trace Context headers (`traceparent: 00-{trace_id}-{span_id}-01`) are injected and propagated across external HTTP boundaries.

2. **Cloud Logging JSON Schema & Context Injection (`src/mangomas/telemetry/logs.py`)**:
   - When `MANGOMAS_LOG__FORMAT=json`, `JsonFormatter` formats stdout and stderr into Google Cloud Logging compliant JSON objects.
   - `TraceContextFilter` automatically inspects the active OpenTelemetry span:
     - Inside an active span: Injects `trace_id` (128-bit trace ID formatted as a 32-character lowercase hex string) and `span_id` (64-bit span ID formatted as a 16-character lowercase hex string).
     - Outside an active span: Safely populates `trace_id` and `span_id` as empty strings `""`.
   - `CorrelationFilter` injects the active ContextVar `correlation_id` (or fallback `"-"`).
   - Fields map directly to Google Cloud Logging indexed attributes (`timestamp`, `severity`, `logger`, `message`, `trace_id`, `span_id`, `correlation_id`).

3. **Cloud Run Manifest Validation & Rendering (`scripts/deploy_environment.py`)**:
   - `deploy/service.yaml`: Base Cloud Run service manifest definition.
   - `deploy/environments.yaml`: Multi-environment registry specifying overlays for `dev`, `qa`, and `prod`.
   - `validate`: Programmatically and via CLI verifies all environments offline against internal sentinels (`validate-placeholder`), confirming that container specs, secrets, and annotations are syntactically sound.
   - `render`: Applies target environment overlay, substitutes environment variables (`--substitute-from-env`), verifies that the container image matches `--image` via `verify_rendered`, and guarantees zero unrendered `${...}` placeholders exist in the written YAML.

### 8.2 Telemetry Environment Configuration

**PowerShell (Windows)**:
```powershell
$env:MANGOMAS_ENV = "dev"
$env:MANGOMAS_LOG_LEVEL = "DEBUG"
$env:MANGOMAS_LOG__FORMAT = "json"
$env:MANGOMAS_TELEMETRY__EXPORTER = "gcp"
$env:MANGOMAS_TELEMETRY__METRICS_ENABLED = "false"
$env:MANGOMAS_LLM__PROVIDER = "vertex"
$env:MANGOMAS_LLM__PROJECT_ID = "<YOUR_GCP_PROJECT_ID>"
$env:MANGOMAS_LLM__LOCATION = "us-central1"
$env:MANGOMAS_LLM__MODEL = "gemini-2.5-flash"
```

**Bash (Linux/macOS)**:
```bash
export MANGOMAS_ENV="dev"
export MANGOMAS_LOG_LEVEL="DEBUG"
export MANGOMAS_LOG__FORMAT="json"
export MANGOMAS_TELEMETRY__EXPORTER="gcp"
export MANGOMAS_TELEMETRY__METRICS_ENABLED="false"
export MANGOMAS_LLM__PROVIDER="vertex"
export MANGOMAS_LLM__PROJECT_ID="<YOUR_GCP_PROJECT_ID>"
export MANGOMAS_LLM__LOCATION="us-central1"
export MANGOMAS_LLM__MODEL="gemini-2.5-flash"
```

> **Opt-in Cloud Monitoring metrics.** To export OTel counters and histograms alongside traces
> (`MANGOMAS_TELEMETRY__METRICS_ENABLED="true"`), install the separate `pip install -e ".[gcp-monitoring]"`
> extra, enable `monitoring.googleapis.com`, and grant `roles/monitoring.metricWriter` on the target project.

### 8.3 Step 1: Distributed Trace Streaming & Structured JSON Log Verification

Execute an agent turn with verbose diagnostic logging to verify trace context and structured JSON formatting:

**PowerShell**:
```powershell
mangomas chat "Verify distributed trace propagation and Cloud Logging JSON schema." -a chat -v
```

**Bash**:
```bash
mangomas chat "Verify distributed trace propagation and Cloud Logging JSON schema." -a chat -v
```

#### Illustrative Structured JSON Log Stream (stderr, `JsonFormatter`):
```json
{"timestamp": "2026-11-09T23:55:01.102345+00:00", "severity": "DEBUG", "logger": "mangomas.telemetry", "message": "Telemetry configured", "trace_id": "", "span_id": "", "correlation_id": "-", "event": "telemetry_configured", "exporter": "gcp"}
{"timestamp": "2026-11-09T23:55:01.215432+00:00", "severity": "INFO", "logger": "mangomas.agents.chat", "message": "Dispatching agent request to vertex [gemini-2.5-flash]", "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736", "span_id": "00f067aa0ba902b7", "correlation_id": "corr-8f12a3b4"}
{"timestamp": "2026-11-09T23:55:02.348910+00:00", "severity": "DEBUG", "logger": "mangomas.adapters.llm.vertex", "message": "Vertex AI generation completed in 1.13s", "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736", "span_id": "00f067aa0ba902b7", "correlation_id": "corr-8f12a3b4"}
```

#### JSON Field Inspection & Verification:
- **`timestamp`**: ISO-8601 UTC timestamp with microsecond precision.
- **`severity`**: Standard Cloud Logging level string (`DEBUG`, `INFO`, `WARNING`, `ERROR`).
- **`logger`**: Logger module hierarchy (`mangomas.telemetry`, `mangomas.agents.chat`).
- **`message`**: Diagnostic text.
- **`trace_id`**: 32-character hexadecimal string representing the active 128-bit W3C trace ID.
- **`span_id`**: 16-character hexadecimal string representing the active 64-bit span ID.
- **`correlation_id`**: Scoped correlation identifier.
- **Cloud Console Verification**:
  Open Google Cloud Console › **Cloud Trace** › **Trace Explorer**. Filter by project `<YOUR_GCP_PROJECT_ID>`. The trace `4bf92f3577b34da6a3ce929d0e0e4736` displays spans for agent dispatch, vertex completion, and storage persistence with precise millisecond latencies.

### 8.4 Step 2: Offline Cloud Run Manifest Registry Validation

Validate the Cloud Run manifest registry and all environment overlays (`dev`, `qa`, `prod`) offline:

**PowerShell**:
```powershell
python scripts/deploy_environment.py --registry deploy/environments.yaml validate
```

**Bash**:
```bash
python scripts/deploy_environment.py --registry deploy/environments.yaml validate
```

**Expected Output**:
```text
INFO deploy_environment: Environment dev OK: service=mangomas-dev repository=... trigger=...
INFO deploy_environment: Environment qa OK: service=mangomas-qa repository=... trigger=...
INFO deploy_environment: Environment prod OK: service=mangomas repository=... trigger=...
INFO deploy_environment: Registry deploy/environments.yaml valid: 3 environment(s) ['dev', 'qa', 'prod']
```
- **Exit Code**: `0` ($LASTEXITCODE in PowerShell, $? in Bash).
- **Safety Guarantee**: Offline validation renders each environment against sentinels, confirming that overlays match schema contracts without making GCP API calls.

### 8.5 Step 3: Cloud Run Manifest Rendering & Zero-Placeholder Assertion

Render the `dev` environment overlay with environment variable substitution and verify the output:

**PowerShell**:
```powershell
$env:PROJECT_ID = "<YOUR_GCP_PROJECT_ID>"
$env:LLM_MODEL = "gemini-2.5-flash"

python scripts/deploy_environment.py `
  --registry deploy/environments.yaml `
  render `
  --environment dev `
  --image "gcr.io/<YOUR_GCP_PROJECT_ID>/mangomas:dev" `
  --output "rendered-service-dev.yaml" `
  --substitute-from-env PROJECT_ID `
  --substitute-from-env LLM_MODEL
```

**Bash**:
```bash
export PROJECT_ID="<YOUR_GCP_PROJECT_ID>"
export LLM_MODEL="gemini-2.5-flash"

python scripts/deploy_environment.py \
  --registry deploy/environments.yaml \
  render \
  --environment dev \
  --image "gcr.io/<YOUR_GCP_PROJECT_ID>/mangomas:dev" \
  --output "rendered-service-dev.yaml" \
  --substitute-from-env PROJECT_ID \
  --substitute-from-env LLM_MODEL
```

#### Verification of Rendered YAML (`rendered-service-dev.yaml`):
1. **Zero Unrendered Placeholders**:
   Every `${PROJECT_ID}` and `${LLM_MODEL}` placeholder is replaced with real values. No `${...}` string patterns remain anywhere in container env vars, secrets, annotations, or labels.
2. **Container Image Integrity**:
   `verify_rendered` validates that the container image exactly matches `gcr.io/<YOUR_GCP_PROJECT_ID>/mangomas:dev`.
3. **Environment Overlays**:
   Confirms environment variables are properly mapped:
   ```yaml
   env:
     - name: MANGOMAS_ENV
       value: dev
     - name: MANGOMAS_LLM__PROJECT_ID
       value: "<YOUR_GCP_PROJECT_ID>"
     - name: MANGOMAS_LLM__MODEL
       value: "gemini-2.5-flash"
   ```

#### Falsifiability Proof (Missing Environment Variable):
Verify that attempting to render a manifest when a required variable is missing fails loud with exit code 2 (`DeployConfigError`):

**PowerShell**:
```powershell
Remove-Item env:PROJECT_ID -ErrorAction SilentlyContinue
python scripts/deploy_environment.py --registry deploy/environments.yaml render --environment dev --image "gcr.io/demo/mangomas:dev" --output "failed.yaml" --substitute-from-env PROJECT_ID
```

**Bash**:
```bash
unset PROJECT_ID
python scripts/deploy_environment.py --registry deploy/environments.yaml render --environment dev --image "gcr.io/demo/mangomas:dev" --output "failed.yaml" --substitute-from-env PROJECT_ID
```

**Expected Output**:
```text
ERROR deploy_environment: --substitute-from-env: unset environment variable(s) ['PROJECT_ID']
```
*(Deploy configuration error: `--substitute-from-env: unset environment variable(s) ['PROJECT_ID']`)*
- **Exit Code**: `2` (`EXIT_CONFIG_ERROR`).

---

## 9. Operational Troubleshooting & Diagnostic Decision Matrix

| Symptom / Error | Root Cause | Diagnostic Command | Remediation Action |
|---|---|---|---|
| `DefaultCredentialsError: Application Default Credentials not found` | ADC credentials missing on workstation | `make gcp-preflight` (the `adc_credentials` row) | Run `gcloud auth application-default login`. |
| `PERMISSION_DENIED: This API method requires billing to be enabled` (persisted as `error_code="llm_bad_response"`) | Billing disabled on the project **or** its billing account is closed (the project can still report `billingEnabled=true`) | `make gcp-preflight` (the `billing` row) and `gcloud billing accounts list` (`OPEN` column) | Reopen the billing account or link an open one. The CLI currently maps this to `llm_bad_response`, so check billing before debugging prompts. |
| `SERVICE_DISABLED` / `API has not been used in project` | Required API not enabled | `make gcp-preflight` (the `required_apis` row) | Run the `gcloud services enable ...` command it prints. |
| `RefreshError: The credentials have expired or been revoked` | ADC refresh token expired | `make gcp-preflight` (the `adc_credentials` row) | Re-authenticate via `gcloud auth application-default login`. |
| `LLMBadResponse: 404 Model not found` | Model ID retired or mistyped | `gcloud ai models list --region=us-central1` | Set `MANGOMAS_LLM__MODEL=gemini-2.5-flash` in `.env` or environment. |
| `SecretsResolutionError: PermissionDenied` | User lacks SecretAccessor role | `gcloud secrets versions access latest --secret=<NAME> >/dev/null && echo ok` (Bash) or `gcloud secrets versions access latest --secret=<NAME> | Out-Null` (PowerShell; never print the secret) | Grant `roles/secretmanager.secretAccessor` on the target secret or project. |
| `PersistenceError: connection to 127.0.0.1:5432 failed` | Cloud SQL Auth Proxy is not running | `make gcp-preflight` (the `postgres_endpoint` row) | Start proxy via `cloud-sql-proxy <PROJECT>:<REGION>:<INSTANCE> --port 5432` or set `MANGOMAS_DB__PROVIDER=sqlite`. |
| `LLMBadResponse: ValidationError` under `VALIDATE_OUTPUT=true` | Model output contains markdown backticks or invalid JSON | Run command with `--verbose` | Gemini models adhere strictly; if testing against smaller local models, disable `VALIDATE_OUTPUT=false`. |
| `PersistenceError / InvalidArgumentError: Collection expecting embedding with dimension of 384, got 768` | Vertex embeddings written to local Chroma collection | Inspect `MANGOMAS_VECTOR__PERSIST_DIR` | Ensure `MANGOMAS_VECTOR__PERSIST_DIR=./data/chroma_vertex` and `MANGOMAS_VECTOR__COLLECTION=mangomas_vertex` to isolate 768-dim embeddings. |
| `Eval quality gate failed (exit code 3)` | Sub-threshold metric, errored row, or regression vs baseline | Inspect stdout `gate=FAIL (...)` and report JSON | Check score thresholds (`--min-mean-score`, `--min-pass-rate`), baseline delta (`--max-mean-score-drop`), or fix regressed prompt/agent logic. |
| `ImportError: Cloud Trace exporter is not installed` | Missing optional OpenTelemetry Cloud Trace SDK extra | `python -c "import opentelemetry.exporter.cloud_trace"` | Run `pip install -e ".[gcp]"` (the extra that ships `opentelemetry-exporter-gcp-trace`). |
| `ImportError: ... mangomas[gcp-monitoring]` | `MANGOMAS_TELEMETRY__METRICS_ENABLED=true` with `EXPORTER=gcp` without the monitoring extra | `python -c "import opentelemetry.exporter.cloud_monitoring"` | Run `pip install -e ".[gcp-monitoring]"`, enable `monitoring.googleapis.com`, and grant `roles/monitoring.metricWriter` (or set `MANGOMAS_TELEMETRY__METRICS_ENABLED=false`). |
| `ConfigError: Unknown telemetry exporter '<TOKEN>'` | Invalid exporter token passed in `MANGOMAS_TELEMETRY__EXPORTER` | Inspect `MANGOMAS_TELEMETRY__EXPORTER` value | Set `MANGOMAS_TELEMETRY__EXPORTER=gcp` for Google Cloud Trace or `MANGOMAS_TELEMETRY__EXPORTER=console` for local console output. |
| `PermissionDenied (403): Google Cloud Trace API` | Principal lacks Cloud Trace Agent role | `gcloud projects get-iam-policy <PROJECT_ID>` | Grant `roles/cloudtrace.agent` to workstation user or service account. |
| `Workflow configuration error: ...` (Exit code 2) | Malformed workflow JSON definition, invalid node kind, or bad predicate | `mangomas workflow validate -f <path>` | Validate JSON syntax against `WorkflowGraph` schema; verify root node kind is one of `sequence`, `fan_out`, `loop`, `branch`, `agent`. |
| `AgentNotFound: Agent '<NAME>' not found` during workflow run | Workflow node references an unregistered agent name | `mangomas agents` | Update workflow JSON step to reference a registered agent (`chat`, `summarize`, `tool`, `planner`, `reviewer`). |
| `DeployConfigError: --substitute-from-env: unset environment variable(s) ['<VAR>']` (Exit code 2) | Required environment variable for manifest overlay is not exported in shell | In PowerShell: `Get-ChildItem env:<VAR>` / in Bash: `echo $<VAR>` | Export missing variable prior to rendering (e.g. `PROJECT_ID`, `LLM_MODEL`). |
| `DeployConfigError: Rendered manifest image does not match target` | Written manifest container image diverged from `--image` flag | Inspect container `image` field in rendered YAML | Ensure `--image` parameter specifies the intended container repository and tag, and re-render. |

