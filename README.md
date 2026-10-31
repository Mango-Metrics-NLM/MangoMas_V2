# Mango-Mas V2

[![CI](https://github.com/Mango-Metrics-NLM/MangoMas_V2/actions/workflows/ci.yml/badge.svg)](https://github.com/Mango-Metrics-NLM/MangoMas_V2/actions/workflows/ci.yml)

Local-first, modular agent platform built on FastAPI and LM Studio. Designed
for local development today; architected for GCP swap-in without rewriting
core agent contracts.

> **License** Proprietary. All rights reserved. This is a private repository;
> [CONTRIBUTING.md](CONTRIBUTING.md) is for its authorized contributors.

**What this is not.** This FastAPI runtime is not the Hugging Face Gradio demo
([`ianshank/MangoMAS`](https://huggingface.co/spaces/ianshank/MangoMAS),
[`MangoMas-Demo`](https://github.com/Mango-Metrics-NLM/MangoMas-Demo),
[`MangoMAS-MoE-7M`](https://huggingface.co/ianshank/MangoMAS-MoE-7M)), not OFFIS
[`mango-agents`](https://github.com/OFFIS-DAI/mango), and not the hospitality
product [mangometrics.io](https://mangometrics.io/). See
[`docs/analysis/20260912-council-peer-review-rewrite.md`](docs/analysis/20260912-council-peer-review-rewrite.md).

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python **3.11+** | `requires-python = ">=3.11"`; CI tests 3.11 and 3.12; the Docker image is `python:3.11-slim` |
| An LLM backend | [LM Studio](https://lmstudio.ai/) with a model loaded and its local server enabled on `:1234` (default), **or** a GCP project with Vertex AI and the `vertex` extra (see [LLM providers](#llm-providers)) |
| Docker | Optional — only for the container image |
| `make` | Optional — every CI step is also a `Makefile` target (`make help`) |

---

## Quick start

### Windows (PowerShell)

```powershell
# 1. Create venv and install all dev dependencies
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pip install -e ./mango-integration-contracts

# 2. Start LM Studio, load a model, and enable the local server on :1234

# 3. Copy and edit the example env file
Copy-Item .env.example .env
# Set MANGOMAS_LLM__MODEL to your loaded model id, e.g.:
#   MANGOMAS_LLM__MODEL=google/gemma-4-e4b

# 4. Run the API (factory pattern required)
uvicorn mangomas.api.app:create_app --factory --reload

# Or run the CLI
mangomas chat "hello"
```

### bash / macOS / Linux

```bash
# 1. Create venv and install all dev dependencies
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pip install -e ./mango-integration-contracts

# 2. Start LM Studio, load a model, and enable the local server on :1234

# 3. Copy the example env file, then set MANGOMAS_LLM__MODEL to your loaded
#    model id (the shipped value, local-model, will not match it)
cp .env.example .env

# 4. Run the API (factory pattern required)
uvicorn mangomas.api.app:create_app --factory --reload

# Or run the CLI
mangomas chat "hello"
```

### Sample session

Illustrative output — the reply text depends on your model:

```console
$ mangomas chat "Name one benefit of a local-first agent platform."
Your data never leaves the machine, so you can iterate without network
latency or per-token cost.

$ curl -s localhost:8000/agents/chat/invoke \
    -H 'Content-Type: application/json' \
    -d '{"messages": [{"role": "user", "content": "hello"}]}'
{"content": "Hello! How can I help you today?", "agent": "chat", "metadata": {...}}
```

`GET /agents` lists every registered agent name; `GET /readyz` tells you
whether the LLM backend is reachable (see [Troubleshooting](#troubleshooting)).

---

## LLM providers

The LLM provider is selected at runtime via `MANGOMAS_LLM__PROVIDER`. Two
built-in providers ship today; both satisfy the same `LLMClient`,
`StreamingLLMClient`, and `PingableLLMClient` protocols so every agent works
against either with no code change.

### LM Studio (default)

```env
MANGOMAS_LLM__PROVIDER=lmstudio
```

| Variable | Default | Description |
|---|---|---|
| `MANGOMAS_LLM__BASE_URL` | `http://localhost:1234/v1` | LM Studio OpenAI-compatible base URL |
| `MANGOMAS_LLM__MODEL` | `local-model` | Model id as shown in LM Studio |
| `MANGOMAS_LLM__API_KEY` | `lm-studio` | API key (placeholder; LM Studio ignores it) |
| `MANGOMAS_LLM__TIMEOUT_SECONDS` | `60.0` | Per-request timeout |
| `MANGOMAS_LLM__TEMPERATURE` | `0.2` | Sampling temperature |

The adapter targets the **OpenAI-compatible** endpoints
`/v1/chat/completions` and `/v1/models`. LM Studio also exposes a
beta REST API at `/api/v1/chat`; that path is **not** used by this
client.

### Vertex AI (optional extra)

```bash
pip install 'mangomas[vertex]'
```

```env
MANGOMAS_LLM__PROVIDER=vertex
MANGOMAS_LLM__PROJECT_ID=your-gcp-project
MANGOMAS_LLM__LOCATION=us-central1               # default
MANGOMAS_LLM__MODEL=gemini-1.5-flash
# Optional explicit credentials (otherwise Application Default Credentials):
# MANGOMAS_LLM__CREDENTIALS_PATH=/path/to/sa.json
# Or resolved via the SecretsProvider seam:
# MANGOMAS_LLM__SECRET_REF=VERTEX_SA_KEY_JSON
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `MANGOMAS_LLM__PROJECT_ID` | in practice | — | GCP project hosting the Vertex endpoint. Not validated: when unset, the SDK falls back to the ADC / environment project, and fails if there is none |
| `MANGOMAS_LLM__LOCATION` | no | `us-central1` | GCP region |
| `MANGOMAS_LLM__MODEL` | in practice | `local-model` | Gemini model id. The shared default is an LM Studio placeholder, so set it explicitly for Vertex |
| `MANGOMAS_LLM__CREDENTIALS_PATH` | no | — | Path to a service-account JSON key |
| `MANGOMAS_LLM__SECRET_REF` | no | — | Secrets-provider key resolving to the JSON key body |

The Vertex SDK is lazy-imported inside `VertexClient.__init__`, so
`mangomas.adapters.llm.vertex` remains importable even when the extra is
not installed. See [docs/adapters/vertex.md](docs/adapters/vertex.md) for
the full error-mapping matrix, auth precedence, and logging events.

---

## API endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/healthz` | Liveness probe — returns `{"status": "ok"}` |
| `GET` | `/health` | Alias for `/healthz` (backwards compatibility) |
| `GET` | `/readyz` | Readiness probe — checks LLM + DB connectivity |
| `GET` | `/ready` | Alias for `/readyz` (backwards compatibility) |
| `GET` | `/agents` | List registered agent names |
| `POST` | `/agents/{name}/invoke` | Invoke an agent (buffered response) |
| `POST` | `/agents/{name}/stream` | Stream agent response as Server-Sent Events |
| `GET` | `/history` | Recent persisted turns (bounded `limit` query; HTTP twin of `mangomas history`) |
| `POST` | `/workflows/run` | Execute a declarative workflow graph (spec 0008 / ADR-0012) |
| `POST` | `/workflows/validate` | Parse + validate a graph without LLM I/O |

### Opt-in HTTP hardening (all default-OFF)

Additive middleware / dependencies, installed only when configured — the default
surface is byte-identical:

| Feature | Enable via | Behaviour |
|---|---|---|
| **Auth** (ADR-0014) | `MANGOMAS_AUTH__ENABLED=true` + `MANGOMAS_AUTH__SECRET_REF` | Bearer / `X-API-Key` check on data + execution routes (probes stay open); fail-closed |
| **CORS** | `MANGOMAS_API__CORS_ALLOW_ORIGINS='["https://app"]'` | `CORSMiddleware`; methods/headers/credentials are env-driven (credentials default off) |
| **Backpressure** (ADR-0015) | `MANGOMAS_API__MAX_BODY_BYTES` / `__MAX_CONCURRENT_REQUESTS` | `413` on oversized body; `503` (reject-don't-queue) at capacity |
| **Metrics** (ADR-0013) | `MANGOMAS_TELEMETRY__METRICS_ENABLED=true` | OTel `MeterProvider` — agent invocation / error / duration instruments |
| **Multi-tenancy** (ADR-0017) | `MANGOMAS_TENANCY__ENABLED=true` | `X-Tenant-ID` → tenant-scoped conversation storage (row filter in SQLite/Postgres) |

### SSE streaming envelope

Each token is delivered as an SSE `data:` frame with a JSON payload:

```
data: {"event": "token", "data": {"content": "Hello"}, "content": "Hello"}

data: {"event": "metadata", "data": {"agent": "chat", "degraded": false, "chunks": 1}}

data: {"event": "done"}
```

The `metadata` frame precedes `done`; `degraded` is `true` when the agent has
no native `stream()` and its buffered reply was sent as a single chunk. An
error after the stream has started cannot become an HTTP error envelope (the
`200` headers are already sent), so the stream simply ends with no `done`
frame — treat a missing `done` as failure.

The top-level `content` key is included for backwards compatibility with
consumers that have not yet adopted the `event`/`data` structure.

---

## Agent registry

Agents are registered via the module-level `agent_registry` in
`src/mangomas/composition/`.  Adding a new agent requires:

1. Implement the `Agent` protocol in `src/mangomas/agents/`.
2. Register a factory in the `composition/` package:

```python
from mangomas.composition import agent_registry

agent_registry.register("my-agent", lambda settings: MyAgent(settings=settings))
```

No changes to `Orchestrator`, `app.py`, or any existing agent are needed.

### Built-in agents

Registered by default (`DEFAULT_AGENTS` in `composition/agents.py`); call them
by the **registered name** in the first column.

| Name | Class | What it does | Streams natively |
|---|---|---|---|
| `chat` | `ChatAgent` | Sends the conversation (plus an optional system prompt) to the LLM and returns the reply | yes |
| `summarize` | `SummarizeAgent` | Loads recent persisted turns (`HISTORY_LIMIT`, default 10) and asks the LLM to summarize them with the request | no |
| `tool` | `ToolAgent` | Runs an inner loop: parse a tool-call block from the reply, execute the tool, feed the result back; capped by `MAX_TOOL_STEPS` (default 5). Tools come from `ctx.tools`, e.g. the RAG `retrieve` tool | no |
| `planner` | `PlannerAgent` | Returns JSON in the `ExecutionPlan` shape: `{goal, steps: [{step, description, agent?}]}` | yes |
| `reviewer` | `ReviewerAgent` | Returns JSON in the `ReviewResult` shape: `{passed, score, feedback, suggestions}` | yes |

Per-agent overrides use `MANGOMAS_AGENTS__<NAME>__*` (system prompt,
temperature, max tokens, model). `planner` / `reviewer` output is validated
against its schema only when `MANGOMAS_AGENTS__<NAME>__VALIDATE_OUTPUT=true`.
Streaming a non-streaming agent still works: the reply arrives as one chunk and
the SSE `metadata` frame carries `"degraded": true`.

---

## Evaluation harness

`mangomas.eval` provides an offline evaluation harness that drives a JSONL
dataset through any registered agent and scores each prediction against an
expected answer.

```bash
mangomas eval \
  --dataset path/to/dataset.jsonl \
  --scorer exact_match \
  --agent chat \
  --output-json eval-output/report.json
```

Built-in scorers (registered through `mangomas.eval.scorer_registry`):

| Scorer | Behaviour |
|---|---|
| `exact_match` | Strict string match with case-folding + whitespace normalisation toggles |
| `regex_match` | Pattern match against the prediction, with configurable regex flags |
| `contains` | Substring containment, with case-folding toggle |
| `json_keys` | Schema conformance — asserts the required keys in structured JSON output (`planner` / `reviewer`) |
| `llm_judge` | Routes a structured JSON prompt through the orchestrator's LLM; pass = `score >= threshold` |
| `embedding` | Cosine similarity of embeddings; resolves a real provider via `ScorerContext.embeddings` |
| `cost_budget` | Estimates USD per row (explicit `cost_usd`, token counts, or output chars). Measure-only unless `max_cost_usd` is set; gate via `MANGOMAS_EVAL__MAX_MEAN_COST_USD` |

`EvalSettings` is wired into top-level `Settings` with the
`MANGOMAS_EVAL__*` env prefix; the CLI flags fall back to those values. See
[docs/eval/harness.md](docs/eval/harness.md) for the dataset schema,
`Scorer` protocol, logging events, and known gaps.

---

## Retrieval-augmented generation (opt-in)

RAG adds two protocol seams (`EmbeddingClient`, `VectorStoreRepository`) and a
pure-domain `rag/` package. It is **off by default** — both
`MANGOMAS_EMBEDDINGS__ENABLED` and `MANGOMAS_VECTOR__ENABLED` are `false`, so
existing deployments behave identically. Enabling it makes the previously
stubbed `embedding` scorer operational and gives agents a `retrieve` tool.

### Local backend (no server required)

```bash
pip install -e ".[dev,embeddings-local,rag]"
```

```env
MANGOMAS_EMBEDDINGS__ENABLED=true
MANGOMAS_EMBEDDINGS__PROVIDER=sentence_transformers
MANGOMAS_EMBEDDINGS__MODEL=all-MiniLM-L6-v2
MANGOMAS_VECTOR__ENABLED=true
```

```bash
mangomas rag ingest ./docs                       # *.md/*.txt → chunk → embed → upsert
mangomas rag query "how does the harness work?"  # prints top-k ranked context
mangomas eval --scorer embedding -d data.jsonl   # real cosine scores
```

### Embedding providers

| `MANGOMAS_EMBEDDINGS__PROVIDER` | Extra | Notes |
|---|---|---|
| `lmstudio` (default) | _(built-in)_ | POST `{base_url}/embeddings`; reuses the LM Studio endpoint |
| `sentence_transformers` | `embeddings-local` | In-process; lazy SDK, runs `encode` off-thread |
| `vertex` | `vertex` | `text-embedding-004`; **ADC auth only** (no service-account JSON) |

| Variable | Default | Description |
|---|---|---|
| `MANGOMAS_EMBEDDINGS__MODEL` | `local-model` | Embedding model id (set per provider) |
| `MANGOMAS_EMBEDDINGS__BATCH_SIZE` | `32` | Pipeline embed-batch size |
| `MANGOMAS_EMBEDDINGS__TIMEOUT_SECONDS` | `60.0` | httpx timeout (LM Studio) |
| `MANGOMAS_VECTOR__PROVIDER` | `chroma` | Vector backend |
| `MANGOMAS_VECTOR__PERSIST_DIR` | `./data/chroma` | Chroma persistent directory |
| `MANGOMAS_VECTOR__COLLECTION` | `mangomas` | Collection name |
| `MANGOMAS_VECTOR__TOP_K` | `5` | Default retrieval depth |
| `MANGOMAS_VECTOR__MAX_TOP_K` | `50` | Ceiling on any per-call retrieval depth |
| `MANGOMAS_RAG__CHUNK_WORDS` | `800` | Chunk size (words) |
| `MANGOMAS_RAG__CHUNK_OVERLAP` | `120` | Overlap (words); validated `< chunk_words` |

The Chroma collection is created in cosine space (`hnsw:space=cosine`) and
similarity is reported as `1 - distance / 2`, so `VectorMatch.score` stays in
`[0, 1]`. Re-ingesting a document first deletes its prior chunks by source, so a
shortened document never leaves orphaned chunks behind. SDKs are lazy-imported,
so `mangomas.adapters.embeddings` / `.vector` stay importable without the extras.

---

## Declarative workflow graphs (opt-in)

Compose agents through a declarative JSON graph consumed by the `Orchestrator`.
Server-configured graphs are default-OFF (but see the inline-definition note
below the table). A `WorkflowGraph` is a bounded
tree — a `sequence` of `agent` / `fan_out` / `loop` / `branch` steps — compiled
down to the existing `dispatch_pipeline` / `dispatch_fan_out` / acceptance-loop
primitives. Every leaf is one public dispatch call, so an all-agent `sequence` is
identical to the imperative `dispatch_pipeline`. The `branch` node (spec 0012 /
ADR-0016) routes on the threaded content via a predicate, enabling
`planner → route → specialised agent`. Predicates come in three kinds —
`contains` and `regex` match the response text, and `json_field` (spec 0032 /
ADR-0034) tests a parsed field, which is the only correct way to loop or route
on a structured agent such as `planner` or `reviewer`. A `fan_out` branch may itself be a
composite (spec 0013 / ADR-0018) — an all-agent fan_out keeps byte-identical
`dispatch_fan_out` parity, while a composite branch runs through its own
executor. Graphs are also runnable over HTTP (`POST /workflows/run|validate`).
See `docs/workflow/graphs.md`, specs 0005/0008/0012/0013, and ADRs
0011/0012/0016/0018.

```bash
export MANGOMAS_WORKFLOW__ENABLED=true
export MANGOMAS_WORKFLOW__DEFINITION=./graph.json   # a path, or inline JSON
mangomas workflow validate                          # parse-only, no LLM I/O
mangomas workflow run "ship the feature"            # prints the final node's reply
```

| Env var | Default | Purpose |
|---|---|---|
| `MANGOMAS_WORKFLOW__ENABLED` | `false` | Enable declarative graph dispatch |
| `MANGOMAS_WORKFLOW__DEFINITION` | _(none)_ | Path to a JSON graph, or inline JSON |
| `MANGOMAS_WORKFLOW__ALLOW_INLINE_DEFINITION` | `true` | Allow a caller-supplied inline graph on `POST /workflows/run` |

> **`ENABLED=false` does not close `POST /workflows/run`.** A request that
> carries its own inline `definition` runs even when the feature is disabled,
> because `ALLOW_INLINE_DEFINITION` defaults to `true`. Set it to `false` to
> require server-configured graphs only (ADR-0033) — recommended for any
> deployment where one shared credential should not authorize composing and
> running an arbitrary agent graph.

The feature adds no new error types: a malformed graph is a `ConfigError` (400),
an unknown agent is `AgentNotFound` (404), and loop exhaustion is
`MaxStepsExceeded` (422). `errors.py` and the `composition/` package are unchanged.

---

## Cognitive signals (opt-in)

Mango-Mas can emit `CognitiveSignal` **1.1.0** envelopes (planner
`planning.proposal`, reviewer `review.finding`) for the sibling
[Mango Code Agent Harness](https://github.com/ianshank/Mango_Code_Agent-Harness).
Default-OFF so existing deployments are byte-identical. This is **not** the
Claude Code harness (`MANGOMAS_HARNESS__*`): cognition proposes; the sibling
harness disposes (INV-16). The sink hangs on `AgentContext.extras["cognitive_sink"]`
— no new `AgentContext` field.

The Hub MoE checkpoint (see [What this is not](#mango-mas-v2)) is a separate
research artifact; `routing.recommendation` is a reserved payload schema, not a
capability grant.

```env
MANGOMAS_SIGNAL__ENABLED=true
MANGOMAS_SIGNAL__DIR=./data/cognitive-signals
MANGOMAS_SIGNAL__GENAI_SPANS=false
# optional HTTP ingest when the harness route exists:
# MANGOMAS_SIGNAL__HTTP_URL=https://harness.example.test/ingest/cognitive
```

`make install` also editable-installs `./mango-integration-contracts` so
flag-on emission can `import mango_contracts` outside pytest's `pythonpath`.
The production image builds both wheels.

| Env var | Default | Purpose |
|---|---|---|
| `MANGOMAS_SIGNAL__ENABLED` | `false` | Attach the JSONL sink and emit after planner/reviewer `handle` |
| `MANGOMAS_SIGNAL__DIR` | `./data/cognitive-signals` | JSONL directory (`signals.jsonl`) |
| `MANGOMAS_SIGNAL__SCHEMA_VERSION` | `1.1.0` | Rejects `1.0.0` at Settings parse (no silent coerce) |
| `MANGOMAS_SIGNAL__GENAI_SPANS` | `false` | Additive `gen_ai.invoke_agent` span alias |
| `MANGOMAS_SIGNAL__HTTP_URL` | _(none)_ | Optional POST of the envelope JSON |

Sink failures are logged and swallowed. Streaming does not emit. `tool` has
no harness role (`retrieve` stays local RAG).

---

## Harness tracing (opt-in) and contributor tooling

Two separate things share the word "harness":

- **Runtime harness tracing** — opt-in, off by default. Production callers
  behave identically until the switch is flipped:

  ```env
  MANGOMAS_HARNESS__ENABLED=true
  MANGOMAS_HARNESS__METRICS_NAMESPACE=mangomas.harness
  MANGOMAS_HARNESS__HOOK_LOG_LEVEL=INFO
  ```

  When enabled, `build_orchestrator` returns a `_HarnessOrchestrator`
  wrapper that adds a `harness.agent_invoke` parent span (with
  `agent.name`, `harness.topology`, `messages.count` attributes) above the
  existing `orchestrator.*` spans. Otherwise, the wrapper is bypassed and
  no extra spans, logs, or hooks are emitted by the runtime process.

- **Contributor tooling** — the Claude Code agent and skill corpus under
  `.claude/`, its hooks, the frontmatter linter and the `.mcp.json` servers.
  It is always in the tree, has no effect on the running service, and is
  documented for contributors in [CLAUDE.md](CLAUDE.md) (which imports
  [AGENTS.md](AGENTS.md)) and
  [`docs/tooling/claude-code-ecosystem.md`](docs/tooling/claude-code-ecosystem.md).
  `docs/architecture/c2-container.md` and `c3-component.md` show where it sits
  in the C4 model.

---

## Troubleshooting

Start with `GET /readyz`: it returns `503` with
`{"ready": false, "checks": [{"name": "llm", "status": "error", "detail": "…"}, …]}`
when the LLM ping or the database check fails.

Typed Mango-Mas errors and the backpressure rejections (`413` / `503`) share one
envelope, `{"error": "<code>", "message": "…"}`, plus a `"detail"` key only when
there is one. Request-validation failures are FastAPI's own `422`, shaped
`{"detail": [{"loc": …, "msg": …, "type": …}, …]}` — check the status before
parsing the body.

On the CLI, the same typed errors print one line,
`<Command> failed: <message> (<detail>)`, and exit `1`. Configuration problems,
including a missing optional extra, exit `2`.

| Symptom | Likely cause | Fix |
|---|---|---|
| API `503` `llm_unavailable`; `/readyz` LLM check `error`; CLI `Chat failed: LM Studio unreachable at …`, exit `1` | LM Studio not running, or its local server is off | Start the server on `:1234`, or point `MANGOMAS_LLM__BASE_URL` at it |
| API `502` `llm_bad_response` ("LM Studio returned HTTP …") while `/readyz` is green | `MANGOMAS_LLM__MODEL` does not match a loaded model (`/readyz` only lists models, so it cannot catch this) | Set the model id exactly as `GET {base_url}/models` reports it |
| API `504` `step_timeout` | One agent step — every LLM call it makes, plus tool calls — exceeded `MANGOMAS_LOOP__STEP_TIMEOUT_SECONDS` (default `30.0`) | Raise `MANGOMAS_LOOP__STEP_TIMEOUT_SECONDS`. With the defaults this deadline fires **before** the LLM timeout, so raising only the LLM timeout changes nothing |
| API `504` `llm_timeout` | A single LLM call exceeded `MANGOMAS_LLM__TIMEOUT_SECONDS` (default `60.0`). Reachable only once the step timeout is above it | Raise `MANGOMAS_LLM__TIMEOUT_SECONDS`, keeping it below the step timeout |
| CLI `Missing optional dependency: Vertex AI SDK is not installed …`, exit `2` (the API fails at startup with the same `ImportError`) | `MANGOMAS_LLM__PROVIDER=vertex` without the extra | `pip install 'mangomas[vertex]'` |
| Vertex fails at startup with a project or credentials error | No `MANGOMAS_LLM__PROJECT_ID` and no ADC project, or no credentials | Set `PROJECT_ID`; run `gcloud auth application-default login` or set `MANGOMAS_LLM__CREDENTIALS_PATH` |
| `mangomas rag ingest` / `rag query` exits `2`: "RAG is not enabled" | Embeddings or vector store disabled | Set both `MANGOMAS_EMBEDDINGS__ENABLED=true` and `MANGOMAS_VECTOR__ENABLED=true` |
| CLI `Missing optional dependency:` naming `chromadb` or `sentence-transformers`, exit `2` | RAG enabled without its extras | `pip install -e ".[rag,embeddings-local]"` |
| CLI exits `2` with a configuration message | Invalid `MANGOMAS_*` value | Fix the variable named in the message; `.env.example` documents each settings group |

---

## Docker

```bash
# Build
docker build -t mangomas .

# Run (exposes port 8000; override with -e PORT=...)
docker run -p 8000:8000 --env-file .env mangomas
```

The image runs as a non-root user, honours `$PORT`, and includes a
HEALTHCHECK that polls `/healthz`.

---

## Quality gates

All gates must pass before merging. The `Makefile` wraps the exact commands
CI runs, so one target reproduces the whole pipeline locally:

```powershell
make gate     # validate-config + lint + format-check + typecheck + lint-imports
              # + frontmatter + protected-paths + test + coverage
              # + bridge-coverage + contracts-coverage + scripts-coverage
make help     # list every target
```

Individually — note the lint surface includes `eval_harness_bridge/src` and
`mango-integration-contracts/src`, matching `.github/workflows/ci.yml`:

```powershell
make lint            # python -m ruff check src tests scripts eval_harness_bridge/src mango-integration-contracts/src
make format-check    # python -m ruff format --check ...
make typecheck       # python -m mypy --strict ...
make lint-imports    # import-linter (core ↛ outer; sibling independence)
make frontmatter     # python scripts/lint_agent_frontmatter.py
make protected-paths # BREAKING-CHANGE trailer on protected core files
make test            # python -m pytest -q  (addopts supply --cov + the global floor)
make coverage        # python scripts/check_coverage.py  — per-package floors
make bridge-coverage # eval_harness_bridge isolated 100% floor
make contracts-coverage # mango-integration-contracts isolated 100% floor
make scripts-coverage # scripts/ isolated floor (Makefile SCRIPTS_FLOOR)
make precommit       # pre-commit run --all-files (subset of the gate; see below)
```

`make secret-scan` (gitleaks) is CI-only and deliberately outside `make
gate` — every other gate step runs fully offline, and downloading a pinned
release binary is the one exception. Run it directly to reproduce that CI job
locally (needs network access).

Scheduled and repository automation outside the gate:
`.github/workflows/nightly.yml` (Postgres suite + secret scan; files a tracking
issue on failure), `.github/dependabot.yml` (monthly `github-actions` and `pip`
updates), and the mandatory checklist in `.github/PULL_REQUEST_TEMPLATE.md`.

**Hooks ≠ gate.** The Claude Code Stop hook is
`make typecheck format-check` plus `pytest --no-cov`. Pre-commit is ruff,
mypy (`src/` only), frontmatter, `validate-config`, and `lint-imports`.
Neither is `make gate`; run `make gate` before opening a PR.

Per-package floors (`scripts/check_coverage.py` — the authoritative gate):
`errors`, `registry`, `core`, `secrets`, `correlation`, `tenancy`, `_headers`,
`utils`, `_entry_points` at **100 %**; `composition`, `agents`, `api`, `cli`,
`config`, `telemetry`, `metrics`, `eval`, `rag`, `workflow`, `cognitive`,
`harness` at **95 %**; `adapters` at **85 %**; global at **95 %**. Scripts are
a separate measured floor (`SCRIPTS_FLOOR` = 94 %). `make gpu` (`RUN_GPU=1`)
fails when no CUDA device is visible. `make guard-probe` is the falsifiability
probe and is not part of `make gate`. The
`--cov-fail-under` in `pyproject.toml` mirrors the global floor for local
runs.

> **Current baseline:** comfortably above every floor — run
> `python -m pytest -q && python scripts/check_coverage.py` for the live numbers.
> Never lower a floor to land a change; fix the test coverage in the same commit.

### Integration tests

```powershell
# Requires no external service — uses in-process ASGI transport; CI runs it on every push
$env:RUN_INTEGRATION = '1'
python -m pytest tests/integration --no-cov
```

### LM Studio smoke tests

```powershell
# Requires a running LM Studio server (see configuration above)
$env:RUN_LMSTUDIO = '1'
$env:LMSTUDIO_MODEL = 'google/gemma-4-e4b'  # or your loaded model
python -m pytest tests/lmstudio --no-cov
```

### Vertex AI E2E tests

```powershell
# Requires a reachable Vertex project and the `vertex` optional extra
pip install '.[vertex]'
$env:RUN_VERTEX = '1'
$env:VERTEX_PROJECT_ID = 'your-gcp-project'
$env:VERTEX_LOCATION = 'us-central1'
$env:VERTEX_MODEL = 'gemini-1.5-flash'
python -m pytest tests/vertex --no-cov
```

---

## Project layout

```
src/mangomas/
  core/         Domain: agent protocol, orchestrator, tool models
  adapters/     llm/ (lmstudio, vertex), embeddings/, vector/, storage/ — swappable for GCP (see ADR-001)
                Shared, underscore-prefixed helpers sit at the package root:
                _http_errors.py / _vertex_errors.py (error translation),
                _openai_client.py (OpenAI-compatible httpx lifecycle),
                embeddings/_shared.py (embed / aclose mixins)
  agents/       Concrete agents: chat, summarize, tool (tool_agent.py), planner, reviewer
  rag/          Opt-in RAG layer — chunker, loader, ingestion pipeline, retriever + RetrievalTool
  workflow/     Opt-in declarative workflow graphs — frozen node models, predicate
                compiler, node registry + executors, loader (compiles to dispatch)
  api/          FastAPI app factory, routes, middleware, auth, health checks
  cli/          Typer CLI, one module per dependency layer behind a permanent
                facade (ADR-0019 / spec-0015 R1) — chat, history, eval, rag,
                workflow subcommands
  eval/         Offline evaluation harness — Scorer protocol, registry, runner, scorers, sinks
  secrets/      SecretsProvider seam (env-var backend; cloud backends pluggable)
  config/       Pydantic-settings, one module per domain behind a permanent
                re-export facade — all config is env-driven (MANGOMAS_*)
  composition/  Composition root — wires registries at startup
  correlation.py  Per-request correlation id ContextVar + filter
  tenancy.py    Opt-in tenant ContextVar for tenant-scoped storage
  errors.py     Typed error hierarchy (MangomasError subclasses)
  harness/      Claude Code harness governance (protected paths, ConfigChange audit)
  _headers.py   Shared HTTP header sanitization (correlation, tenancy)
  _entry_points.py Shared entry-point iteration for eval plugin discovery
  registry.py   Generic, protocol-checked provider store
  metrics.py    Opt-in OTel MeterProvider + agent metrics
  telemetry/    OpenTelemetry configuration, one module per dependency layer
                behind a permanent re-export facade
  utils/        Shared helpers (clock, exception text, regex flags)

tests/
  (root)         Unit tests, named after the module under test
  regression/    AQA regression suite; guards triaged origin defect fixes
  adapters/      Adapter unit tests incl. the shared error/client helpers
  agents/        Agent unit tests
  deploy/        CI/Makefile parity, workflow hardening, deploy manifests,
                 Docker build context, and the gitleaks ruleset contract
  eval/          Evaluation-harness tests (in-process)
  harness/       Protected-path governance + ConfigChange decision table
  rag/           RAG domain tests
  cognitive/     CognitiveSignal producer (roles, PDP, sinks, emit)
  tooling/       Corpus contracts (agents/skills/settings/.mcp.json),
                 the C4 architecture contract, and the collection-gate meta-test
  eval_harness_bridge/  Black-box bridge tests; own 100% floor, own constants.py
  mango_contracts/      CognitiveSignal 1.1.0 envelope tests; own 100% floor, own constants.py
  integration/   ASGITransport-based; set RUN_INTEGRATION=1
  lmstudio/      Real-server tests; set RUN_LMSTUDIO=1
  vertex/        Real Vertex project tests; set RUN_VERTEX=1
  postgres/      Testcontainers-backed; set RUN_POSTGRES=1
  constants/     Shared constants package — re-exports config defaults from mangomas.config
  fakes.py       Protocol-accurate test doubles

docs/
  adr/           Architecture Decision Records
  adapters/      Per-adapter usage docs (vertex.md)
  analysis/      One-off assessments (dated; historical records, not live docs)
  architecture/  C4 diagrams (Mermaid Context, Container, Component, Code) + observability + cloud providers
  eval/          Evaluation harness usage (harness.md)
  workflow/      Declarative workflow-graph usage (graphs.md)
  plans/         Multi-milestone delivery sequencing
  testing/       Regression baseline and per-suite scenario plans
  tooling/       Claude Code ecosystem catalog (MCP servers, hooks, rejections)

specs/           One thin spec per non-trivial feature, written before the code
scripts/         Coverage gate, frontmatter lint, harness hook entry points, deploy-environment planner
deploy/          Cloud Run base manifest + per-environment registry (dev / qa / prod, ADR-0036)
.claude/         Agents, skills and settings — the live Claude Code corpus
.github/         CI, deploy, eval-gate and nightly workflows + dependabot
```

---

## Further reading

- [ADR-001: Cloud Target Swap Matrix](docs/adr/0001-cloud-targets.md)
- [C4 Architecture: System Context](docs/architecture/c1-context.md)
- [C4 Architecture: Code Domain Model](docs/architecture/c4-code.md)
- [Vertex AI Adapter](docs/adapters/vertex.md)
- [Evaluation Harness](docs/eval/harness.md)
- [Observability](docs/architecture/observability.md)
- [Regression Baseline](docs/testing/regression.md)
- [LM Studio E2E Scenario Plan](docs/testing/lmstudio-e2e.md)
- [Declarative Workflow Graphs](docs/workflow/graphs.md)
- [Claude Code Ecosystem Tooling](docs/tooling/claude-code-ecosystem.md)
- [Specs index](specs/README.md) — spec-before-code, one file per feature
- [Changelog](CHANGELOG.md)
- [Next Steps & Roadmap](NEXT_STEPS.md)
