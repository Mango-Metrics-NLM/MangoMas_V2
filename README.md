# Mango-Mas V2

Local-first, modular agent platform built on FastAPI and LM Studio. Designed
for local development today; architected for GCP swap-in without rewriting
core agent contracts.

> **License** Proprietary. All rights reserved.

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
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pip install -e ./mango-integration-contracts
cp .env.example .env
uvicorn mangomas.api.app:create_app --factory --reload
```

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
| `MANGOMAS_LLM__PROJECT_ID` | yes | — | GCP project hosting the Vertex endpoint |
| `MANGOMAS_LLM__LOCATION` | no | `us-central1` | GCP region |
| `MANGOMAS_LLM__MODEL` | yes | — | Gemini model id |
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

data: {"event": "done"}
```

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
| `MANGOMAS_RAG__CHUNK_WORDS` | `800` | Chunk size (words) |
| `MANGOMAS_RAG__CHUNK_OVERLAP` | `120` | Overlap (words); validated `< chunk_words` |

The Chroma collection is created in cosine space (`hnsw:space=cosine`) and
similarity is reported as `1 - distance / 2`, so `VectorMatch.score` stays in
`[0, 1]`. Re-ingesting a document first deletes its prior chunks by source, so a
shortened document never leaves orphaned chunks behind. SDKs are lazy-imported,
so `mangomas.adapters.embeddings` / `.vector` stay importable without the extras.

---

## Declarative workflow graphs (opt-in)

Compose agents through a declarative JSON graph consumed by the `Orchestrator`,
default-OFF so existing deployments see no change. A `WorkflowGraph` is a bounded
tree — a `sequence` of `agent` / `fan_out` / `loop` / `branch` steps — compiled
down to the existing `dispatch_pipeline` / `dispatch_fan_out` / acceptance-loop
primitives. Every leaf is one public dispatch call, so an all-agent `sequence` is
identical to the imperative `dispatch_pipeline`. The `branch` node (spec 0012 /
ADR-0016) routes on the threaded content via a predicate, enabling
`planner → route → specialised agent`. A `fan_out` branch may itself be a
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

This FastAPI runtime is **not** the Hugging Face Gradio demo
([`ianshank/MangoMAS`](https://huggingface.co/spaces/ianshank/MangoMAS),
[`MangoMas-Demo`](https://github.com/Mango-Metrics-NLM/MangoMas-Demo),
[`MangoMAS-MoE-7M`](https://huggingface.co/ianshank/MangoMAS-MoE-7M)),
not OFFIS [`mango-agents`](https://github.com/OFFIS-DAI/mango), and not
the hospitality product [mangometrics.io](https://mangometrics.io/).
The Hub MoE checkpoint is a separate research artifact;
`routing.recommendation` is a reserved payload schema, not a capability
grant. Identity and eval-honesty notes:
[`docs/analysis/20260912-council-peer-review-rewrite.md`](docs/analysis/20260912-council-peer-review-rewrite.md).

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

## Claude Code harness (opt-in)

The repository ships an enterprise Claude Code harness configured under
`.claude/skills/`, `.claude/agents/`, and
`.claude/settings.json`. It is **opt-in** — production callers behave
identically until the switch is flipped:

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

What ships in the harness:

| Surface | Path | Status |
|---|---|---|
| Skills (workflow helpers) | `.claude/skills/<name>/SKILL.md` | 17 skills — live in Claude Code and VS Code Copilot |
| Agents | `.claude/agents/mango-<slug>.md` | 27 agents, flat: 4 routers + 23 specialists |
| Frontmatter linter | `scripts/lint_agent_frontmatter.py` | CI + local pre-commit gate |
| SessionStart hook | `scripts/harness_session_start.py` | Probe venv + LM Studio reachability |
| Project settings | `.claude/settings.json` | Allow/Deny + SessionStart/PreToolUse/PostToolUse/Stop hooks |
| MCP servers | `.mcp.json` | 6 servers: filesystem/git/fetch/sequential-thinking/repomix + optional github |
| Cross-session memory (per-contributor, user-scoped) | external `~/.claude-mem/` | claude-mem — no shared config |
| Secret scan | `.gitleaks.toml` + `make secret-scan` | Declared ruleset; working tree + history; proved non-vacuous nightly |
| Scheduled automation | `.github/workflows/nightly.yml` | Postgres suite + secret scan; files a tracking issue on failure |
| Dependency updates | `.github/dependabot.yml` | Monthly `github-actions` + root `pip` + `/mango-integration-contracts` pip |
| PR template | `.github/PULL_REQUEST_TEMPLATE.md` | Mandatory PR checklist |

See `CLAUDE.md` for the full skill/agent map and protected-path
table; `docs/tooling/claude-code-ecosystem.md` for the full external
tooling catalog (MCP servers, hooks, rejected/reference-only tools);
`docs/architecture/c2-container.md` and `c3-component.md` for where the
harness sits in the C4 model.

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

**Hooks ≠ gate.** The Claude Code Stop hook is
`make typecheck format-check` plus `pytest --no-cov`. Pre-commit is ruff,
mypy (`src/` only), frontmatter, `validate-config`, and `lint-imports`.
Neither is `make gate`; run `make gate` before opening a PR.

Per-package floors (`scripts/check_coverage.py` — the authoritative gate):
`errors`, `registry`, `core`, `secrets`, `correlation`, `tenancy`, `_headers`
at **100 %**; `composition`, `agents`, `api`, `cli`, `eval`, `rag`, `workflow`,
`cognitive` at **95 %**; `adapters` at **85 %**; global at **95 %**. The
`--cov-fail-under` in `pyproject.toml` mirrors the global floor for local
runs.

> **Current baseline:** comfortably above every floor — run
> `python -m pytest -q && python scripts/check_coverage.py` for the live numbers.

Coverage today sits comfortably above each floor — never lower a
floor to land a change, fix the test coverage in the same commit.

### Integration tests

```powershell
# Requires no external service — uses in-process ASGI transport
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
  agents/       Concrete agents: chat, summarize, tool_agent, planner, reviewer
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
scripts/         Coverage gate, frontmatter lint, and the harness hook entry points
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
