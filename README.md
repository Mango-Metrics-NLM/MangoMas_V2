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
`src/mangomas/composition.py`.  Adding a new agent requires:

1. Implement the `Agent` protocol in `src/mangomas/agents/`.
2. Register a factory in `composition.py`:

```python
from mangomas.composition import agent_registry

agent_registry.register("my-agent", lambda settings: MyAgent(settings=settings))
```

No changes to `Orchestrator`, `app.py`, or any existing agent are needed.

### Out-of-tree agents (entry-point discovery)

A separate package can register an agent **without modifying this repo** by
declaring an entry point under the `mangomas.agents` group:

```toml
[project.entry-points."mangomas.agents"]
my-agent = "mypkg.module:agent_factory"   # Callable[[AgentSettings | None], Agent]
```

Set `MANGOMAS_DISCOVERY_ENABLED=true` and the agent is discovered and wired at
orchestrator-build time. Discovery is off by default and fault-isolated — a broken
plugin is logged and skipped, never breaking startup.

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
| `llm_judge` | Routes a structured JSON prompt through the orchestrator's LLM; pass = `score >= threshold` |
| `embedding` | Cosine similarity of embeddings; requires an LLM with `.embed()` (gap documented in harness docs) |

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
| `MANGOMAS_RAG__MIN_CHUNK_WORDS` | `50` | Drop trailing fragments shorter than this |

The Chroma collection is created in cosine space (`hnsw:space=cosine`) and
similarity is reported as `1 - distance / 2`, so `VectorMatch.score` stays in
`[0, 1]`. Re-ingesting a document first deletes its prior chunks by source, so a
shortened document never leaves orphaned chunks behind. SDKs are lazy-imported,
so `mangomas.adapters.embeddings` / `.vector` stay importable without the extras.

---

## Claude Code harness (opt-in)

The repository ships an enterprise Claude Code harness configured under
`.github/agents/` (parents + sub-agents), `.github/skills/`, and
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
| Skills (workflow helpers) | `.github/skills/<name>/SKILL.md` | 8 skills |
| Parent agents | `.github/agents/<parent>.agent.md` | 4 parents |
| Sub-agents (opt-in `sub_agents:` key) | `.github/agents/<parent>/<slug>.agent.md` | 12 specialised sub-agents |
| Frontmatter linter | `scripts/lint_agent_frontmatter.py` | CI + local pre-commit gate |
| SessionStart hook | `scripts/harness_session_start.py` | Probe venv + LM Studio reachability |
| Project settings | `.claude/settings.json` | Allow/Deny + Stop/PostToolUse hooks |
| PR template + secret-scan job | `.github/PULL_REQUEST_TEMPLATE.md` + `ci.yml` | Mandatory PR checklist |

See `CLAUDE.md` for the full skill/sub-agent map and protected-path
table; `docs/architecture/c2-container.md` and `c3-component.md` for
where the harness sits in the C4 model.

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

All gates must pass before merging:

```powershell
# Lint
python -m ruff check src tests scripts

# Format check
python -m ruff format --check src tests scripts

# Type check (strict)
python -m mypy --strict src tests scripts

# Tests + coverage (95% global floor; per-package floors layered on top)
python -m pytest

# Per-package coverage floors
python scripts/check_coverage.py
```

Per-package floors (`scripts/check_coverage.py`): `errors`, `registry`,
`core`, `secrets`, `correlation` at **100 %**; `composition`, `agents`,
`api`, `cli`, `eval`, `rag` at **95 %**; `adapters` at **85 %**; global at
**95 %**.

> **Current baseline:** 639 tests, **97.95 %** global coverage (RAG port + hardening).

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
  agents/       Concrete agents: chat, summarize, tool_agent, planner, reviewer
  rag/          Opt-in RAG layer — chunker, loader, ingestion pipeline, retriever + RetrievalTool
  api/          FastAPI app factory, routes, middleware, health checks
  cli/          Typer CLI (chat, history, eval, rag ingest|query subcommands)
  eval/         Offline evaluation harness — Scorer protocol, registry, runner, scorers
  secrets/      SecretsProvider seam (env-var backend; cloud backends pluggable)
  config.py     Pydantic-settings — all config is env-driven (MANGOMAS_*)
  composition.py  Composition root — wires registries at startup
  correlation.py  Per-request correlation id ContextVar + filter
  telemetry.py  OpenTelemetry configuration

tests/
  unit/          (inline with src naming) — mocked dependencies
  integration/   ASGITransport-based; set RUN_INTEGRATION=1
  lmstudio/      Real-server tests; set RUN_LMSTUDIO=1
  vertex/        Real Vertex project tests; set RUN_VERTEX=1
  eval/          Evaluation-harness tests (in-process)

docs/
  adr/           Architecture Decision Records
  adapters/      Per-adapter usage docs (vertex.md)
  architecture/  C4 diagrams (Mermaid) + observability
  eval/          Evaluation harness usage (harness.md)
  testing/       Regression baseline and LM Studio scenario plan
```

---

## Further reading

- [ADR-001: Cloud Target Swap Matrix](docs/adr/0001-cloud-targets.md)
- [C4 Architecture Diagrams](docs/architecture/)
- [Vertex AI Adapter](docs/adapters/vertex.md)
- [Evaluation Harness](docs/eval/harness.md)
- [Observability](docs/architecture/observability.md)
- [Regression Baseline](docs/testing/regression.md)
- [LM Studio E2E Scenario Plan](docs/testing/lmstudio-e2e.md)
- [Changelog](CHANGELOG.md)
- [Next Steps & Roadmap](NEXT_STEPS.md)
