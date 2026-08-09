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

# Tests (unit; addopts supply --cov + the global 95 % floor)
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

Every CI command is also wrapped as a `Makefile` target — `make gate` runs the
whole pipeline (lint, format-check, typecheck, frontmatter, test, per-package
coverage, bridge coverage) in CI's order; `make help` lists the rest. Prefer it
over retyping paths: CI's lint surface is
`src tests scripts eval_harness_bridge/src`, which is wider than the
`src tests` shown above.

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
│   ├── _prompt.py      resolve_system_prompt + build_messages (shared precedence + message insertion)
│   ├── _structured.py  StructuredOutputAgent — shared base for planner/reviewer
│   ├── chat.py         ChatAgent
│   ├── summarize.py    SummarizeAgent
│   ├── tool_agent.py   ToolAgent (inner tool-execution loop)
│   ├── planner.py      PlannerAgent (ExecutionPlan structured output)
│   └── reviewer.py     ReviewerAgent (ReviewResult structured output)
├── adapters/
│   ├── _http_errors.py  Shared httpx → typed-error translator (llm + embeddings)
│   ├── _vertex_errors.py Shared Vertex qualname error matrix (llm + embeddings)
│   ├── _openai_client.py OpenAICompatHTTPClient — shared httpx lifecycle base
│   │                    (_request / _log_and_translate: POST/GET → raise → log → translate)
│   ├── llm/            LLMClient protocol + LMStudioAdapter + VertexClient
│   ├── embeddings/     EmbeddingClient protocol + lmstudio / sentence_transformers / vertex
│   │                   (_shared.py: embed / aclose mixins — backends write embed_batch only)
│   ├── vector/         VectorStoreRepository protocol + VectorMatch + ChromaVectorStore
│   └── storage/        TurnRepository + MemoryRepository protocols + impls
├── rag/            Pure-domain RAG layer (opt-in; imports only protocols + models)
│   ├── models.py       Chunk, SearchResult (frozen dataclasses)
│   ├── chunker.py      Word-window chunker (pure fn)
│   ├── loader.py       file/dir → raw docs (asyncio.to_thread)
│   ├── pipeline.py     IngestionPipeline: load→chunk→embed_batch→upsert
│   └── retrieval.py    Retriever + RetrievalTool (satisfies Tool)
├── workflow/       Declarative workflow-graph layer (opt-in; spec 0005)
│   ├── graph.py        Frozen node models + WorkflowNode union + WorkflowGraph
│   ├── predicate.py    PredicateSpec + compile_predicate → sync AcceptanceFn
│   ├── registry.py     node_registry + resolve_executor
│   ├── nodes/_factory.py make_node_factory — shared typed factory + guard
│   ├── executor.py     NodeExecutor protocol + execute_workflow driver
│   ├── loader.py       path/inline JSON → WorkflowGraph (ConfigError boundary)
│   └── nodes/          Self-registering agent/sequence/fan_out/loop/branch executors
├── api/
│   ├── app.py          FastAPI app factory (lifespan, middleware installation)
│   ├── errors.py       Error-status mapping, error-envelope builder
│   ├── models.py       DTO models (WorkflowRunRequest, ValidateRequest/Response)
│   ├── middleware.py   MaxBodySize, ConcurrencyLimit, Tenancy, AccessLog
│   └── routes/         Endpoint routers by resource
│       ├── agents.py   invoke, stream endpoints (dispatches to orchestrator)
│       ├── system.py   /health, /ready, /list_agents endpoints
│       └── workflows.py /workflows/run, /workflows/validate endpoints
├── cli/main.py     Typer CLI (chat, history, eval, rag, workflow commands)
├── composition.py  Composition root — wires settings → adapters → orchestrator
├── config.py       Pydantic-settings: Settings, LLMSettings, DBSettings,
│                   LoopSettings, MemorySettings, EmbeddingSettings,
│                   VectorSettings, RagSettings
├── errors.py       Typed error hierarchy (MangomasError subclasses)
├── registry.py     Registry[T] — generic, protocol-checked provider store
├── telemetry.py    OpenTelemetry setup (OTLP or console exporter)
├── _headers.py     Shared HTTP header sanitization (correlation, tenancy)
├── _entry_points.py Shared entry-point iteration for eval plugin discovery
└── metrics.py      Instrumentation registry (singleton, double-checked lock)
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
| `MANGOMAS_LLM__PROJECT_ID` | _(none)_ | GCP project id (required when `PROVIDER=vertex`) |
| `MANGOMAS_LLM__LOCATION` | `us-central1` | GCP region for Vertex |
| `MANGOMAS_DB__PROVIDER` | `sqlite` | Storage registry entry; `postgres` enables Cloud SQL |
| `MANGOMAS_DB__URL` | `sqlite:///./data/mangomas.db` | Turn-storage database |
| `MANGOMAS_DB__POOL_MIN` | `1` | asyncpg pool minimum |
| `MANGOMAS_DB__POOL_MAX` | `10` | asyncpg pool maximum |
| `MANGOMAS_SECRETS__PROVIDER` | `env` | Secrets registry entry; `gcp` enables Secret Manager |
| `MANGOMAS_SECRETS__PROJECT_ID` | _(none)_ | GCP project id (required when `PROVIDER=gcp`) |
| `MANGOMAS_SECRETS__STRICT` | `false` | Raise `SecretsResolutionError` on cloud secret failures instead of returning `None` |
| `MANGOMAS_API__CORS_ALLOW_ORIGINS` | `[]` | Opt-in CORS allow-list; empty → `CORSMiddleware` not installed |
| `MANGOMAS_API__MAX_BODY_BYTES` | `0` | Max request body bytes (`0` = off; 413 when exceeded; ADR-0015) |
| `MANGOMAS_API__MAX_CONCURRENT_REQUESTS` | `0` | Max in-flight requests (`0` = off; 503 when saturated; ADR-0015) |
| `MANGOMAS_AUTH__ENABLED` | `false` | Enforce bearer / API-key auth on data + execution routes (ADR-0014) |
| `MANGOMAS_AUTH__SECRET_REF` | _(none)_ | `SecretsProvider` ref resolving to the expected API token (required when enabled) |
| `MANGOMAS_TENANCY__ENABLED` | `false` | Tenant-scoped conversation storage via a row filter (ADR-0017) |
| `MANGOMAS_TENANCY__HEADER` | `X-Tenant-ID` | Inbound tenant header → per-request `ContextVar` |
| `MANGOMAS_TENANCY__DEFAULT` | `default` | Implicit tenant when the header is absent/disabled |
| `MANGOMAS_TELEMETRY__METRICS_ENABLED` | `false` | Install an OTel `MeterProvider` (agent invocation/error/duration; ADR-0013) |
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
| `MANGOMAS_EVAL__DATASET_SOURCE` | `jsonl` | Dataset source (`jsonl`/`inline`/`langfuse`) resolved via `dataset_source_registry` |
| `MANGOMAS_EVAL__DATASET_SOURCE_OPTIONS` | `{}` | Per-source options keyed by source name (e.g. `{"inline": {"rows": [...]}}`) |
| `MANGOMAS_EVAL__GATE_ENABLED` | `false` | Engage the CI quality gate (exit 3 on fail) |
| `MANGOMAS_EVAL__MIN_MEAN_SCORE` | _(none)_ | Gate threshold on `mean_score` `[0,1]` |
| `MANGOMAS_EVAL__MIN_PASS_RATE` | _(none)_ | Gate threshold on `passed/size` `[0,1]` |
| `MANGOMAS_EVAL__FAIL_ON_ERROR` | `false` | Gate fails if any row errored |
| `MANGOMAS_EVAL__BASELINE_PATH` | _(none)_ | Baseline report JSON to diff against (regression gating) |
| `MANGOMAS_EVAL__MAX_MEAN_SCORE_DROP` | _(none)_ | Regression gate: max allowed `mean_score` drop vs baseline `[0,1]` |
| `MANGOMAS_EVAL__MAX_PASS_RATE_DROP` | _(none)_ | Regression gate: max allowed `pass_rate` drop vs baseline `[0,1]` |
| `MANGOMAS_EVAL__ALLOW_NEW_FAILURES` | `true` | Regression gate: fail (exit 3) on rows that passed in baseline but fail now when `false` |
| `MANGOMAS_EVAL__SINKS` | `["console"]` | Result sinks (`console`/`json_file`/`sqlite_results`/`webhook`/`langfuse`) |
| `MANGOMAS_EVAL__SCHEMA_VERSION` | `1` | Forward-compatible eval-config version marker |
| `MANGOMAS_DISCOVERY_ENABLED` | `false` | Enable entry-point discovery of eval scorer/sink/target/source plugins |
| `MANGOMAS_WORKFLOW__ENABLED` | `false` | Enable declarative workflow-graph dispatch |
| `MANGOMAS_WORKFLOW__DEFINITION` | _(none)_ | Path to a JSON graph, or inline JSON |
| `MANGOMAS_AGENTS__<NAME>__SYSTEM_PROMPT` | _(none)_ | Per-agent system-prompt override (`AgentSettings.system_prompt`) |
| `MANGOMAS_AGENTS__<NAME>__TEMPERATURE` | _(none)_ | Per-agent sampling override, forwarded to `LLMClient.complete`/`stream` |
| `MANGOMAS_AGENTS__<NAME>__MAX_TOKENS` | _(none)_ | Per-agent completion cap, forwarded via the additive `max_tokens` keyword |
| `MANGOMAS_AGENTS__<NAME>__MAX_TOOL_STEPS` | `5` (`DEFAULT_TOOL_MAX_STEPS`) | `ToolAgent`-only: cap on total LLM calls per request |
| `MANGOMAS_AGENTS__<NAME>__MODEL_OVERRIDE` | _(none)_ | Reserved — not read by any agent yet; per-agent model selection needs a composition-layer change (spec-0015) |

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
- **Dataset sources** (`eval/dataset_source.py` + `eval/sources/`, registered in
  `dataset_source_registry`): `jsonl` (default — wraps `load_jsonl`), `inline`
  (rows via options), and the optional `langfuse` source (extra
  `mangomas[langfuse]`). Selected via `--dataset-source`. See ADR-0004.
- **Gate** (`eval/gate.py`): pure `evaluate_gate(report, ...) -> GateResult`.
  CLI adds **exit code 3** on failure (distinct from 1=runtime, 2=config), raised
  only after sinks emit. Off unless a threshold / `gate_enabled` / `fail_on_error`
  is set.
- **Regression gate** (`eval/baseline.py` + `eval/gate.py`): `load_baseline` reads
  a prior `json_file` report; pure `diff_reports` → `ReportDiff`;
  `evaluate_regression_gate(diff, ...)` fails (exit 3) on a `mean_score`/`pass_rate`
  drop beyond tolerance or new row failures. `--baseline` + `--max-*-drop` /
  `--no-allow-new-failures`; threshold + regression verdicts combine via
  `merge_gate_results`. See ADR-0005.
- **Sinks** (`eval/sink.py` + `eval/sinks/`, registered in `sink_registry`):
  `console`, `json_file`, `sqlite_results` (append report + rows to SQLite),
  `webhook` (httpx POST), and the optional `langfuse` sink (extra
  `mangomas[langfuse]`, lazy-imported, `LANGFUSE_*` env/ADC; `per_row` option
  emits one trace/score per row). Multiple sinks compose under per-sink fault
  isolation. `--output-json` injects `json_file`.
- **Plugins** (`eval/discovery.py`): entry-point groups `mangomas.eval.scorers` /
  `mangomas.eval.sinks` / `mangomas.eval.targets` / `mangomas.eval.dataset_sources`;
  discovered only when `MANGOMAS_DISCOVERY_ENABLED=true`; iterates entry points via
  the shared `mangomas._entry_points`.
- **Shared helpers**: `eval/_langfuse.py` (client bootstrap reused by the
  Langfuse sink and dataset source); `eval/_options.py`
  (`require_str`/`require_list`/`require_unit_float` — factory-time option
  validation shared across sinks/sources/targets).

CLI: `mangomas eval -d <dataset> -s <scorer> [-t <target>] [--dataset-source <src>] [-o report.json]`.
Gated tests: `RUN_LANGFUSE=1` (Langfuse sink).

---

## Error Types

```python
MangomasError           # base; has .code str, .message, .detail
├── AgentNotFound       # code="agent_not_found"
├── ConfigError         # code="config_error"; invalid config
│   └── UnknownProvider # code="unknown_provider"
├── LLMError            # code="llm_error" (base for LLM errors)
│   ├── LLMBadResponse  # code="llm_bad_response"
│   ├── LLMTimeout      # code="llm_timeout"
│   └── LLMUnavailable  # code="llm_unavailable"
├── MaxStepsExceeded    # code="max_steps_exceeded"; .steps int
├── PersistenceError    # code="persistence_error"; file/DB I/O failures
├── SecretsResolutionError  # code="secrets_resolution_error"; .ref, .provider (503; strict mode)
├── ToolNotFound        # code="tool_not_found"; .name, .available
└── ToolExecutionError  # code="tool_execution_error"; .tool_name
```

HTTP status mapping is centralised in `api/errors.py::_ERROR_STATUS`.

---

## Testing Conventions

- **Framework**: `pytest` with `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed)
- **Coverage gate**: `scripts/check_coverage.py` is the single source of truth —
  95 % global minimum plus per-package floors
  (`errors`/`registry`/`core`/`secrets`/`correlation` = 100 %,
  `adapters` = 85 %, rest = 95 %). The pytest `--cov-fail-under=95` addopt in
  `pyproject.toml` mirrors the global floor.
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

## Spec-Driven Development

Non-trivial features get a **spec before code** under `specs/`. Copy
`specs/TEMPLATE.md` to `specs/NNNN-kebab-slug.md` (next free integer, mirroring
the `docs/adr/` numbering), fill in Problem / Requirements / Config-env /
Protocol-contract impact / Backwards-compat / Test plan / Acceptance criteria,
and link an ADR when a boundary changes. Specs are thin and **not**
CI-enforced — see `specs/README.md`. `docs/adr/` records decisions;
`docs/plans/` records multi-milestone sequencing.

---

## Claude Code Sub-Agents

Each parent agent in `.github/agents/<parent>.agent.md` may declare specialised
sub-agents via the optional `sub_agents:` frontmatter list. Sub-agent files
live alongside the parent in `.github/agents/<parent>/<name>.agent.md`.

| Parent | Sub-agents |
|--------|-----------|
| `architect` | `protocol-auditor`, `layering-auditor`, `adr-author`, `pr-watcher` |
| `backend` | `llm-adapter-dev`, `storage-adapter-dev`, `orchestrator-dev`, `error-taxonomy-dev`, `telemetry-exporter-dev`, `workflow-graph-dev` |
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
| `MANGOMAS_HARNESS__METRICS_EXPORTER` | `inherit` | Harness span exporter (`inherit`/`console`/`gcp`); `inherit` reuses the app exporter |
| `MANGOMAS_HARNESS__HOOK_LOG_LEVEL` | `INFO` | Level for SessionStart-hook log records |
| `MANGOMAS_TELEMETRY__EXPORTER` | `console` | Application span exporter (`console`/`gcp` Cloud Trace) |

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
  `src/mangomas/core/orchestrator.py`, `src/mangomas/core/tools.py`,
  `src/mangomas/errors.py`, `src/mangomas/registry.py`) with a required
  `BREAKING-CHANGE` marker on staged diffs (the legacy
  `# approved-breaking-change` marker is still accepted). Wired into CI
  as `frontmatter-lint`.
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
| `mango-topology` | Composing pipelines, fan-outs, acceptance loops (imperative) |
| `mango-workflow` | Declarative workflow graphs: schema, nodes, predicates, `workflow` CLI |
| `mango-rag` | Embeddings/vector/RAG: ingestion, retrieval, RetrievalTool wiring |
| `mango-eval` | Evaluation harness: scorers, sinks, targets, sources, gate/baseline |
| `mango-deploy` | Cloud Run deploy + telemetry-exporter selection (GCP swap) |
| `mango-release` | Drafting CHANGELOG, PR description, pre-merge checklist |

---

## Claude Code MCP Servers & Ecosystem Tooling

Project-scoped MCP servers are declared in `.mcp.json` (repo root). In a
human's interactive terminal session they connect after a one-time
workspace-trust approval (`claude mcp list` to check status; `/mcp` to
approve pending servers); **cloud/Agent-SDK sessions skip that prompt
entirely and load them with no approval step** — see
`docs/tooling/claude-code-ecosystem.md` before assuming otherwise.

| Server | Use for |
|--------|---------|
| `filesystem` | Reading/listing files scoped to the repo root |
| `git` | `git log`/`blame`/`diff` introspection via MCP instead of Bash |
| `fetch` | Retrieving a URL's content directly (e.g. upstream library docs) |
| `sequential-thinking` | Structured multi-step reasoning for planning-heavy tasks |
| `repomix` | Pack a directory into one context-efficient bundle before a cross-cutting refactor |

`rtk` (if installed locally) transparently compacts noisy Bash stdout via a
`PreToolUse` hook; the hook guards on `command -v rtk`, so if the binary is
absent it is skipped silently and Bash tool calls work exactly as before
(see ADR-0020). A contributor can opt out individually
with `MANGOMAS_DISABLE_RTK_HOOK=1` in their personal
`.claude/settings.local.json`, since Claude Code has no per-hook disable.

Cross-session memory (`claude-mem`) and the `claude-hud` statusline are
**opt-in and local-only**; `claude-mem`'s shared hooks and `claude-hud`'s
final disposition are both pending one hands-on step each — see
`docs/tooling/claude-code-ecosystem.md` for current status. Neither is
required to work on this repo. `zilliztech/claude-context` was evaluated and
explicitly **rejected** (redundant with this repo's own `rag/` + Chroma
stack; sends code to third parties by default) — see ADR-0020 before
re-proposing it.

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

For the **declarative** equivalent (compose these topologies from JSON), see
"Declarative Workflow Graphs" below and the `mango-workflow` skill.

---

## Declarative Workflow Graphs (opt-in)

Off by default (`MANGOMAS_WORKFLOW__ENABLED=false`), so existing deployments see
no change. A `WorkflowGraph` (JSON) is a bounded tree compiled to the imperative
dispatch primitives above — `sequence` of `agent` / `fan_out` / `loop` / `branch`
(predicate-routed selection; spec 0012 / ADR-0016), where every leaf is one public
dispatch call (no reimplemented loop/gather). See spec 0005 / ADR-0011 and
`docs/workflow/graphs.md`.

- **Model** (`workflow/graph.py`) — frozen Pydantic discriminated union;
  metadata-transparent executors, so an all-agent `sequence` equals
  `dispatch_pipeline`.
- **Predicate** (`workflow/predicate.py`) — `PredicateSpec` (`contains`/`regex`)
  compiles once to a pure sync `AcceptanceFn`.
- **Registry** (`workflow/registry.py`) — `node_registry` (mirrors
  `eval.target_registry`); seeded by `import mangomas.workflow`.
- **Errors** reuse `ConfigError` (400) / `AgentNotFound` (404) /
  `MaxStepsExceeded` (422) — `errors.py` unchanged.

CLI: `mangomas workflow validate -f graph.json` and `mangomas workflow run "<msg>"
-f graph.json`. Both exit `2` when disabled and no `--definition` is passed.

```python
from mangomas.workflow import execute_workflow, load_workflow
graph = load_workflow("graph.json")          # or an inline JSON string
response = await execute_workflow(graph, request, orch=orchestrator)
```

---

## File Ownership

| Path | Change with care |
|------|-----------------|
| `src/mangomas/core/agent.py` | Stable public contract — backward-compat required (protected path) |
| `src/mangomas/core/orchestrator.py` | Dispatch surface — backward-compat required (protected path) |
| `src/mangomas/core/tools.py` | Tool contracts + parser — backward-compat required (protected path) |
| `src/mangomas/errors.py` | Typed error hierarchy + HTTP mapping (protected path) |
| `src/mangomas/registry.py` | Generic, no project-specific logic (protected path) |
| `src/mangomas/composition.py` | Single wiring point — all new adapters registered here |
| `tests/fakes.py` | Shared test doubles — keep minimal and protocol-accurate |

Paths marked _(protected path)_ are gated by the `lint_agent_frontmatter.py`
hook: a staged edit requires a `BREAKING-CHANGE` marker in the diff.
