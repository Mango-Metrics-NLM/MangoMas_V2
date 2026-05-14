# Core Domain — `src/mangomas/core/`

This directory contains the **stable public contracts** of the platform.
Changes here must be backward-compatible unless a breaking change is documented
in `CHANGELOG.md` under `### Breaking Changes`.

## Contents

| File | Purpose |
|------|---------|
| `agent.py` | `Agent` protocol, `AgentContext`, `AgentRequest`, `AgentResponse`, `Message` |
| `orchestrator.py` | `Orchestrator` — dispatch, iterative loop, pipeline, fan-out |
| `tools.py` | `ToolSpec`, `ToolCall`, `ToolResult`, `Tool` protocol, `ToolCallParser`, prompt builders |
| `loop.py` | `AcceptanceFn` type alias |

## Key Contracts

```python
class Agent(Protocol):
    name: str
    async def handle(self, request: AgentRequest, ctx: AgentContext) -> AgentResponse: ...

@dataclass
class AgentContext:
    llm: LLMClient
    repo: TurnRepository | None
    tools: ToolRegistry | None = None
    memory: MemoryRepository | None = None
    extras: dict[str, Any] = field(default_factory=dict)
```

## Rules

- **Backward-compatible only**: new fields on `AgentRequest` / `AgentResponse` must have defaults.
- **No concrete types**: `AgentContext` annotates fields with Protocol types under `TYPE_CHECKING`.
- **`from __future__ import annotations`** required in every file here.
- Cross-layer imports (e.g. `LLMClient`) are guarded by `if TYPE_CHECKING:`.

## Multi-Agent Topologies

```python
# Iterative loop with acceptance criterion (raises MaxStepsExceeded if never satisfied)
response = await orch.dispatch("chat", request, acceptance_fn=lambda r: "DONE" in r.content, max_steps=5)

# Sequential pipeline
response = await orch.dispatch_pipeline(["planner", "tool", "reviewer"], request)

# Parallel fan-out → list[AgentResponse]
responses = await orch.dispatch_fan_out(["reviewer", "summarize"], request)
```
