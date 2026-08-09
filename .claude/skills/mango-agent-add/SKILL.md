---
name: mango-agent-add
description: >
  Adding a new agent to Mango-Mas V2. Use when: implementing a new
  Agent satisfying the core Agent Protocol, registering it in the agent
  registry, wiring it via composition.py, and producing the matching test
  file. Covers the Agent / StreamingAgent contract, AgentContext
  injection, and the 4-step extension pattern documented in CLAUDE.md.
argument-hint: "Describe the agent (e.g. 'critic agent that scores responses') or paste a draft implementation"
---

# Mango-Mas Agent Skill

## When to Use

- Implement a new domain agent (planner, critic, summariser variant, etc.)
- Add a streaming-capable agent satisfying `StreamingAgent`
- Wire a new agent into `composition.py::agent_registry`
- Diagnose `AgentNotFound` errors at dispatch time
- Convert an existing single-shot agent to support iterative loops via `AcceptanceFn`

---

## Quick Commands

```powershell
# Run only the new agent's tests
python -m pytest tests/test_<agent>.py -v

# Verify the agent registers and dispatches end-to-end
python -m pytest tests/test_orchestrator.py -v

# Full suite + coverage gate
python -m pytest --tb=short -q
```

```bash
python -m pytest tests/test_<agent>.py tests/test_orchestrator.py -v
python -m pytest --tb=short -q
```

---

## Agent Rules

| Rule | Detail |
|------|--------|
| Protocol-first | Satisfy `Agent` in `src/mangomas/core/agent.py`. Streaming agents additionally satisfy `StreamingAgent`. |
| Stateless | All state lives in `AgentContext` (LLM, repo, memory, tools). Never store request-scoped data on the agent instance. |
| Single registration point | Register via `agent_registry.register("<name>", factory)` in `composition.py` only. |
| Config-driven settings | Per-agent settings (system prompt, temperature override) live in `AgentSettings` in `config.py`, read at construction time. |
| Structured output | Use Pydantic v2 models for any structured output (see `PlannerAgent.ExecutionPlan`, `ReviewerAgent.ReviewResult`). |
| Telemetry | Open a span via `get_tracer(__name__).start_as_current_span("agent.<name>.execute")`. |
| Errors typed | Raise subclasses of `MangomasError` only. Never bare `Exception`. |

---

## Reference

| File | Role |
|------|------|
| `src/mangomas/core/agent.py` | `Agent` & `StreamingAgent` Protocols, `AgentContext`, `AgentRequest`, `AgentResponse`, `Message` |
| `src/mangomas/agents/chat.py` | Reference `ChatAgent` — simplest possible implementation |
| `src/mangomas/agents/summarize.py` | `SummarizeAgent` — system-prompt customisation |
| `src/mangomas/agents/planner.py` | `PlannerAgent` — structured Pydantic output |
| `src/mangomas/agents/reviewer.py` | `ReviewerAgent` — structured output + acceptance loop usage |
| `src/mangomas/agents/tool_agent.py` | `ToolAgent` — inner tool-execution loop |
| `src/mangomas/agents/_streaming.py` | Shared streaming fallback for non-`StreamingLLMClient` backends |
| `src/mangomas/agents/__init__.py` | Re-export new agents so `composition.py` can import them |
| `src/mangomas/composition.py` (`agent_registry.register` calls) | Where the 5 built-in agents are registered — pattern to follow |
| `tests/test_agent.py` | Reference test layout for new agents |

---

## Template — New Agent

```python
# src/mangomas/agents/<name>.py
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mangomas.core.agent import Agent, AgentRequest, AgentResponse
from mangomas.telemetry import get_tracer

if TYPE_CHECKING:
    from mangomas.config import AgentSettings
    from mangomas.core.agent import AgentContext

logger = logging.getLogger(__name__)
_tracer = get_tracer(__name__)


class <Name>Agent:
    """One-line purpose."""

    name = "<name>"

    def __init__(self, settings: AgentSettings | None = None) -> None:
        self._settings = settings

    async def handle(
        self, request: AgentRequest, ctx: AgentContext
    ) -> AgentResponse:
        with _tracer.start_as_current_span("agent.<name>.execute") as span:
            span.set_attribute("agent.name", self.name)
            logger.info("Agent executing", extra={"agent": self.name})
            content = await ctx.llm.complete(request.messages)
            return AgentResponse(content=content, agent=self.name)
```

Re-export from `src/mangomas/agents/__init__.py`:

```python
from mangomas.agents.<name> import <Name>Agent  # noqa: F401
```

Register in `src/mangomas/composition.py`:

```python
agent_registry.register("<name>", lambda settings: <Name>Agent(settings=settings))
```

---

## Workflow (the 4-step pattern from CLAUDE.md)

1. Create `src/mangomas/agents/<name>.py` satisfying the `Agent` Protocol.
2. Re-export in `src/mangomas/agents/__init__.py`.
3. Register the factory in `composition.py::agent_registry`.
4. Write `tests/test_<name>.py` using `FakeLLM` / `FakeRepository` / `FakeTool` from `tests/fakes.py` and constants from `tests/constants.py`.

After: `ruff check --fix`, `mypy --strict`, `pytest --tb=short -q`, then update CHANGELOG.

---

## Constraints

- DO NOT call `ctx.llm`, `ctx.repo`, etc. before checking they are non-`None` if the agent may run with optional components.
- DO NOT subscribe to `AgentContext` types from outside `TYPE_CHECKING:` — break import cycles.
- DO NOT raise bare `Exception` — use `MangomasError` subclasses.
- DO NOT hardcode system prompts or temperatures — read them from `AgentSettings`.
- DO NOT register the agent in any file other than `composition.py`.

---

## Diagnosing Failures

1. `AgentNotFound` → factory not registered or registered under a different slug than the request's `agent` field.
2. mypy `Argument 1 to "register" has incompatible type` → factory signature must be `Callable[[AgentSettings | None], Agent]`.
3. Test assertion `assert isinstance(agent, Agent)` fails → check method signatures match `core/agent.py` exactly (including `async`, return type, parameter names).
4. Coverage gate fails for `agents/<name>.py` (95% floor) → add tests for the unhappy path (LLM raises, empty request, structured output validation error).
