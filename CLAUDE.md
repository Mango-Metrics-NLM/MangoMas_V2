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

# Tests (unit + coverage gate at 85 %)
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
│   ├── llm/            LLMClient protocol + LMStudioAdapter
│   └── storage/        TurnRepository + MemoryRepository protocols + impls
├── api/app.py      FastAPI app (lifespan, /agents/{name}/invoke|stream)
├── cli/main.py     Typer CLI (chat, history commands)
├── composition.py  Composition root — wires settings → adapters → orchestrator
├── config.py       Pydantic-settings: Settings, LLMSettings, DBSettings,
│                   LoopSettings, MemorySettings
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
| `MANGOMAS_LLM__BASE_URL` | `http://localhost:1234/v1` | LM Studio endpoint |
| `MANGOMAS_LLM__MODEL` | `local-model` | Model id |
| `MANGOMAS_LLM__TEMPERATURE` | `0.2` | Sampling temperature |
| `MANGOMAS_DB__URL` | `sqlite:///./mangomas.db` | Turn-storage database |
| `MANGOMAS_LOOP__MAX_STEPS` | `1` | Orchestrator loop cap |
| `MANGOMAS_LOOP__STEP_TIMEOUT_SECONDS` | `30.0` | Per-step timeout |
| `MANGOMAS_MEMORY__ENABLED` | `false` | Enable file-memory |
| `MANGOMAS_MEMORY__PROVIDER` | `file` | Memory backend provider |
| `MANGOMAS_MEMORY__MEMORY_DIR` | `memory` | Memory root directory |

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
- **Coverage gate**: 85 % minimum — enforced by `pytest --cov`
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
