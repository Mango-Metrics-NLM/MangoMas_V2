# Mango-Mas V2 — Claude Code Context

Local-first, modular agent platform built on **FastAPI + LM Studio**.
Designed to run entirely on-device today; architected for GCP swap-in without
rewriting core agent contracts.

---

## Essential Commands

```powershell
# Install (Windows)
python -m venv .venv ; .venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# Run API (factory pattern required)
uvicorn mangomas.api.app:create_app --factory --reload

# Run CLI
mangomas chat "hello"

# Tests (unit + coverage gate at 95 %)
python -m pytest --tb=short -q

# Integration tests (requires LM Studio running)
$env:RUN_INTEGRATION='1' ; python -m pytest tests/integration --no-cov -q

# Lint (auto-fix)
ruff check --fix src tests
ruff format src tests

# Type-check
mypy

# Pre-commit (runs ruff + mypy on staged files)
pre-commit run --all-files
```

---

## Architecture

```
src/mangomas/
├── core/           # Stable domain contracts (Agent, Orchestrator, tools, loop)
│   ├── agent.py        Protocol: Agent, AgentContext, AgentRequest, AgentResponse
│   ├── orchestrator.py Dispatch + iterative loop + pipeline/fan-out topologies
│   ├── tools.py        ToolSpec, ToolCallParser, ToolRegistry, prompt builders
│   └── loop.py         AcceptanceFn type alias
├── agents/         # Concrete agent implementations (all satisfy Agent protocol)
│   ├── chat.py         ChatAgent
│   ├── summarize.py    SummarizeAgent
│   ├── tool_agent.py   ToolAgent (inner tool-execution loop)
│   ├── planner.py      PlannerAgent (ExecutionPlan structured output)
│   └── reviewer.py     ReviewerAgent (ReviewResult structured output)
├── adapters/
│   ├── _http_errors.py  Shared httpx → typed-error translator (llm + embeddings)
│   ├── _vertex_errors.py Shared Vertex qualname error matrix (llm + embeddings)
│   ├── llm/            LLMClient protocol + LMStudioAdapter + VertexClient
│   ├── embeddings/     EmbeddingClient protocol + lmstudio / sentence_transformers / vertex
│   ├── vector/         VectorStoreRepository protocol + VectorMatch + ChromaVectorStore
│   └── storage/        TurnRepository + MemoryRepository protocols + impls
├── rag/            Pure-domain RAG layer (opt-in; imports only protocols + models)
│   ├── models.py       Chunk, SearchResult (frozen dataclasses)
│   ├── chunker.py      Word-window chunker (pure fn)
│   ├── loader.py       file/dir → raw docs (asyncio.to_thread)
│   ├── pipeline.py     IngestionPipeline: load→chunk→embed_batch→upsert
│   └── retrieval.py    Retriever + RetrievalTool (satisfies Tool)
├── api/app.py      FastAPI app (lifespan, /agents/{name}/invoke|stream)
├── cli/main.py     Typer CLI (chat, history, eval, rag ingest|query commands)
├── composition.py  Composition root — wires settings → adapters → orchestrator
├── config.py       Pydantic-settings: Settings, LLMSettings, DBSettings,
│                   LoopSettings, MemorySettings, EmbeddingSettings,
│                   VectorSettings, RagSettings
├── errors.py       Typed error hierarchy (MangomasError subclasses)
├── registry.py     Registry[T] — generic, protocol-checked provider store
└── telemetry.py    OpenTelemetry setup (OTLP or console exporter)
```

---

## Key Design Rules

| Rule | Detail |
|------|--------|
| **Protocol-first** | Every adapter satisfies a `@runtime_checkable Protocol`. Never import concrete types across layers. |
| **No hard-coded values** | All tunables live in `Settings` via env vars (`MANGOMAS_*` prefix). |
| **Backwards-compatible contracts** | `AgentRequest`, `AgentResponse` fields default-safe; adding fields must not break callers. |
| **`from __future__ import annotations`** | Required in every source file. |
| **TYPE_CHECKING guards** | Cross-layer imports (e.g. `LLMClient` in `AgentContext`) live inside `if TYPE_CHECKING:` blocks. |
| **Async I/O** | `asyncio.to_thread` for any synchronous I/O (file, DB) inside async handlers. |
| **Composition root** | All wiring happens in `composition.py::build_orchestrator`. No service locators elsewhere. |

---

## Configuration

All settings are env-driven with prefix `MANGOMAS_`:

| Variable | Default | Purpose |
|---|---|---|
| `MANGOMAS_LLM__PROVIDER` | `lmstudio` | LLM registry entry; `vertex` enables Vertex AI |
| `MANGOMAS_LLM__BASE_URL` | `http://localhost:1234/v1` | LM Studio endpoint |
| `MANGOMAS_LLM__MODEL` | `local-model` | Model id |
| `MANGOMAS_LLM__TEMPERATURE` | `0.2` | Sampling temperature |
| `MANGOMAS_LLM__PROJECT` | _(none)_ | GCP project id (required when `PROVIDER=vertex`) |
| `MANGOMAS_LLM__LOCATION` | `us-central1` | GCP region for Vertex |
| `MANGOMAS_LLM__MAX_OUTPUT_TOKENS` | _(none)_ | Optional Gemini generation_config ceiling |
| `MANGOMAS_DB__PROVIDER` | `sqlite` | Storage registry entry; `postgres` enables Cloud SQL |
| `MANGOMAS_DB__URL` | `sqlite:///./mangomas.db` | Turn-storage database |
| `MANGOMAS_DB__POOL_MIN` | `1` | asyncpg pool minimum |
| `MANGOMAS_DB__POOL_MAX` | `10` | asyncpg pool maximum |
| `MANGOMAS_SECRETS__PROVIDER` | `env` | Secrets registry entry; `gcp` enables Secret Manager |
| `MANGOMAS_SECRETS__PROJECT_ID` | _(none)_ | GCP project id (required when `PROVIDER=gcp`) |
| `MANGOMAS_LOOP__MAX_STEPS` | `1` | Orchestrator loop cap |
| `MANGOMAS_LOOP__STEP_TIMEOUT_SECONDS` | `30.0` | Per-step timeout |
| `MANGOMAS_MEMORY__ENABLED` | `false` | Enable file-memory |
| `MANGOMAS_MEMORY__PROVIDER` | `file` | Memory backend provider |
| `MANGOMAS_MEMORY__MEMORY_DIR` | `memory` | Memory root directory |
| `MANGOMAS_EMBEDDINGS__ENABLED` | `false` | Construct + attach `ctx.embeddings` |
| `MANGOMAS_EMBEDDINGS__PROVIDER` | `lmstudio` | `lmstudio` \| `sentence_transformers` \| `vertex` |
| `MANGOMAS_EMBEDDINGS__MODEL` | `local-model` | Embedding model id (set per provider) |
| `MANGOMAS_EMBEDDINGS__BASE_URL` | `http://localhost:1234/v1` | LM Studio endpoint |
| `MANGOMAS_EMBEDDINGS__API_KEY` | `lm-studio` | LM Studio bearer (placeholder) |
| `MANGOMAS_EMBEDDINGS__BATCH_SIZE` | `32` | Pipeline embed-batch size |
| `MANGOMAS_EMBEDDINGS__TIMEOUT_SECONDS` | `60.0` | httpx timeout (LM Studio) |
| `MANGOMAS_EMBEDDINGS__PROJECT_ID` / `__LOCATION` | _(none)_ / `us-central1` | Vertex only (ADC auth) |
| `MANGOMAS_VECTOR__ENABLED` | `false` | Construct + attach `ctx.vector_store` |
| `MANGOMAS_VECTOR__PROVIDER` | `chroma` | Vector backend |
| `MANGOMAS_VECTOR__PERSIST_DIR` | `./data/chroma` | Chroma persistent dir |
| `MANGOMAS_VECTOR__COLLECTION` | `mangomas` | Collection name |
| `MANGOMAS_VECTOR__TOP_K` | `5` | Default retrieval depth |
| `MANGOMAS_RAG__CHUNK_WORDS` | `800` | Chunk size (words) |
| `MANGOMAS_RAG__CHUNK_OVERLAP` | `120` | Overlap (words); validated `< chunk_words` |
| `MANGOMAS_RAG__MIN_CHUNK_WORDS` | `50` | Drop trailing fragments shorter than this |
| `MANGOMAS_EVAL__SCORER` | `exact_match` | Scorer name (`exact_match`/`regex_match`/`contains`/`json_keys`/`llm_judge`/`embedding`) |
| `MANGOMAS_EVAL__TARGET` | `agent` | Eval target (`agent`/`pipeline`/`fan_out`/`echo`) resolved via `target_registry` |
| `MANGOMAS_EVAL__TARGET_OPTIONS` | `{}` | Per-target options keyed by target name (e.g. `{"pipeline": {"agents": [...]}}`) |
| `MANGOMAS_EVAL__GATE_ENABLED` | `false` | Engage the CI quality gate (exit 3 on fail) |
| `MANGOMAS_EVAL__MIN_MEAN_SCORE` | _(none)_ | Gate threshold on `mean_score` `[0,1]` |
| `MANGOMAS_EVAL__MIN_PASS_RATE` | _(none)_ | Gate threshold on `passed/size` `[0,1]` |
| `MANGOMAS_EVAL__FAIL_ON_ERROR` | `false` | Gate fails if any row errored |
| `MANGOMAS_EVAL__SINKS` | `["console"]` | Result sinks (`console`/`json_file`/`langfuse`) |
| `MANGOMAS_EVAL__SCHEMA_VERSION` | `1` | Forward-compatible eval-config version marker |
| `MANGOMAS_DISCOVERY_ENABLED` | `false` | Enable entry-point discovery of eval scorer/sink/target plugins |

---

## Retrieval-Augmented Generation (opt-in)

RAG is fully opt-in and off by default (`embeddings.enabled` / `vector.enabled`
both `false`), so existing deployments see no behaviour change. Three seams:

- **`EmbeddingClient`** (`adapters/embeddings/base.py`) — `embed` / `embed_batch`
  / `aclose`. Backends: `LMStudioEmbeddingClient` (httpx POST `{base_url}/embeddings`),
  `SentenceTransformersEmbeddingClient` (in-process, lazy SDK), `VertexEmbeddingClient`
  (`text-embedding-004`, **ADC only**).
- **`VectorStoreRepository`** (`adapters/vector/base.py`) — primitives only
  (`ids`/`embeddings`/`documents`/`metadatas` + `VectorMatch`), so the vector
  layer never imports `rag/`. `ChromaVectorStore` forces `hnsw:space=cosine` and
  maps distance→similarity as `1 - d/2` (keeps scores in `[0, 1]`).
- **`rag/`** — pure domain: `chunk_text` word-window chunker, `load_documents`,
  `IngestionPipeline` (delete_by_source → chunk → embed_batch → upsert),
  `Retriever` + `RetrievalTool` (satisfies the `Tool` protocol; auto-discovered
  by `ToolAgent` via `ctx.tools` when both embeddings + vector store are present).

CLI: `mangomas rag ingest <path>` and `mangomas rag query <text>`. When RAG is
disabled both exit `2` with a clear "not enabled" message. The stubbed
`EmbeddingScorer` now resolves a real provider via `ScorerContext.embeddings`.

Extras: `pip install 'mangomas[embeddings-local]'` (sentence-transformers),
`pip install 'mangomas[rag]'` (chromadb); Vertex embeddings reuse the `vertex`
extra. Gated tests: `RUN_EMBEDDINGS_LOCAL=1`, `RUN_RAG=1`.

---

## Evaluation Harness (opt-in)

`mangomas.eval` runs a JSONL dataset through any agent, scores each row with a
pluggable `Scorer`, emits an `EvalReport` to one or more `Sink`s, and optionally
gates the run for CI. Everything is additive and default-OFF. See
`docs/eval/harness.md` and ADR-0003.

- **Scorers** (`eval/scorers/`, registered in `scorer_registry`): `exact_match`,
  `regex_match`, `contains`, `json_keys` (schema-conformance for `planner`/
  `reviewer` JSON output), `llm_judge`, `embedding`.
- **Targets** (`eval/target.py` + `eval/targets/`, registered in `target_registry`):
  `agent` (default — dispatch one agent), `pipeline`, `fan_out`, and `echo`
  (deterministic baseline). `EvalRunner.run` takes an optional `target=`; the
  legacy `agent_name` positional is wrapped in the `agent` target. `EvalReport`
  gains an additive `target_name`. See ADR-0004.
- **Gate** (`eval/gate.py`): pure `evaluate_gate(report, ...) -> GateResult`.
  CLI adds **exit code 3** on failure (distinct from 1=runtime, 2=config), raised
  only after sinks emit. Off unless a threshold / `gate_enabled` / `fail_on_error`
  is set.
- **Sinks** (`eval/sink.py` + `eval/sinks/`, registered in `sink_registry`):
  `console`, `json_file`, and the optional `langfuse` sink (extra
  `mangomas[langfuse]`, lazy-imported, `LANGFUSE_*` env/ADC). Multiple sinks
  compose under per-sink fault isolation. `--output-json` injects `json_file`.
- **Plugins** (`eval/discovery.py`): entry-point groups `mangomas.eval.scorers` /
  `mangomas.eval.sinks` / `mangomas.eval.targets`; discovered only when
  `MANGOMAS_DISCOVERY_ENABLED=true`.

CLI: `mangomas eval -d <dataset> -s <scorer> [-t <target>] [--min-mean-score X] [-o report.json]`.
Gated tests: `RUN_LANGFUSE=1` (Langfuse sink).

---

## Error Types

```python
MangomasError           # base; has .code str
├── AgentNotFound       # code="agent_not_found"
├── LLMBadResponse      # code="llm_bad_response"
├── LLMError            # code="llm_error"
├── MaxStepsExceeded    # code="max_steps_exceeded"; .steps int
├── ToolNotFound        # code="tool_not_found"; .name, .available
└── ToolExecutionError  # code="tool_execution_error"; .tool_name
```

HTTP status mapping is centralised in `api/app.py::_ERROR_STATUS`.

---

## Testing Conventions

- **Framework**: `pytest` with `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed)
- **Coverage gate**: 95 % minimum — enforced by `pytest --cov` (515 tests, 98.16 % current coverage)
- **Fake adapters**: `tests/fakes.py` — `FakeLLM`, `FakeRepository`, `FakeTool`, `FakeMemoryRepository`
- **Constants**: `tests/constants.py` — never use magic strings/numbers in tests
- **No mocking of internal protocols** — use Fake* classes from `fakes.py`
- **Hypothesis fuzz** tests live in `test_tools.py`
- **Integration tests** in `tests/integration/`; gated by `RUN_INTEGRATION=1`

---

## Agent Extension Pattern

To add a new agent:
1. Create `src/mangomas/agents/<name>.py` satisfying the `Agent` protocol
2. Register in `src/mangomas/agents/__init__.py`
3. Register factory in `composition.py` via `agent_registry.register("<name>", ...)`
4. Write `tests/test_<name>.py`

---

## Claude Code Sub-Agents

Each parent agent in `.github/agents/<parent>.agent.md` may declare specialised
sub-agents via the optional `sub_agents:` frontmatter list. Sub-agent files
live alongside the parent in `.github/agents/<parent>/<name>.agent.md`.

| Parent | Sub-agents |
|--------|-----------|
| `architect` | `protocol-auditor`, `layering-auditor`, `adr-author`, `pr-watcher` |
| `backend` | `llm-adapter-dev`, `storage-adapter-dev`, `orchestrator-dev`, `error-taxonomy-dev` |
| `test-engineer` | `fake-builder`, `hypothesis-fuzz`, `integration-runner` |
| `api-dev` | `sse-streamer`, `schema-evolution` |

The `sub_agents:` key is optional and backward-compatible — parents without it
remain valid. Slugs are resolved to `<parent>/<slug>.agent.md`.

## Claude Code Harness (opt-in)

The enterprise harness layer is configured by `HarnessSettings` (env prefix
`MANGOMAS_HARNESS__`) and engaged only when `enabled=True`:

| Variable | Default | Purpose |
|---|---|---|
| `MANGOMAS_HARNESS__ENABLED` | `false` | Wrap dispatch + stream_dispatch in `harness.agent_invoke` |
| `MANGOMAS_HARNESS__METRICS_NAMESPACE` | `mangomas.harness` | OTel tracer namespace for harness spans |
| `MANGOMAS_HARNESS__HOOK_LOG_LEVEL` | `INFO` | Level for SessionStart-hook log records |

When enabled, `composition.py::build_orchestrator` returns
`_HarnessOrchestrator` instead of the bare `Orchestrator`. The subclass
overrides both `dispatch` and `stream_dispatch` to add a
`harness.agent_invoke` parent span with attributes `agent.name`,
`harness.topology` (`dispatch` or `stream`), and `messages.count`.
`dispatch_pipeline` and `dispatch_fan_out` inherit the wrap because
they delegate through `dispatch`.

Two scripts in `scripts/` complete the harness:

- `lint_agent_frontmatter.py` — Pydantic-validated lint of `*.agent.md`
  / `SKILL.md` frontmatter and `sub_agents:` resolution. Also gates
  protected core paths (`src/mangomas/core/agent.py`,
  `src/mangomas/registry.py`, `src/mangomas/core/orchestrator.py`,
  `src/mangomas/core/tools.py`) with a required `BREAKING-CHANGE`
  marker on staged diffs. Wired into CI as `frontmatter-lint`.
- `harness_session_start.py` — SessionStart hook for Claude Code on
  the web. Emits a single-line JSON probe report (venv + LM Studio)
  so a fresh session knows what's available. Always returns `EXIT_OK`.

## Claude Code Skills

Skills are workflow-scoped helpers under `.github/skills/<name>/SKILL.md`.

| Skill | Use when |
|-------|----------|
| `mango-testing` | Writing/running tests, extending fakes, coverage |
| `mango-adapter` | Adding a new LLM/storage/memory/secrets adapter |
| `mango-agent-add` | Adding a new agent following the 4-step pattern |
| `mango-error` | Adding a new error type with HTTP mapping |
| `mango-observability` | Instrumenting with spans + structured logging |
| `mango-config` | Adding a new tunable to `Settings` |
| `mango-topology` | Composing pipelines, fan-outs, acceptance loops |
| `mango-rag` | Embeddings/vector/RAG: ingestion, retrieval, RetrievalTool wiring |
| `mango-release` | Drafting CHANGELOG, PR description, pre-merge checklist |

---

## Multi-Agent Topologies

```python
# Sequential pipeline — output of each agent feeds next
response = await orchestrator.dispatch_pipeline(["planner", "tool", "reviewer"], request)

# Parallel fan-out — all agents receive same request; returns list
responses = await orchestrator.dispatch_fan_out(["reviewer", "summarize"], request)

# Iterative loop with acceptance criterion
from mangomas.core import AcceptanceFn
accept: AcceptanceFn = lambda r: "DONE" in r.content
response = await orchestrator.dispatch("chat", request, acceptance_fn=accept, max_steps=5)
```

---

## File Ownership

| Path | Change with care |
|------|-----------------|
| `src/mangomas/core/agent.py` | Stable public contract — backward-compat required |
| `src/mangomas/registry.py` | Generic, no project-specific logic |
| `src/mangomas/composition.py` | Single wiring point — all new adapters registered here |
| `tests/fakes.py` | Shared test doubles — keep minimal and protocol-accurate |
