# Agents — `src/mangomas/agents/`

Concrete agent implementations. Every file here satisfies the `Agent` protocol
from `core/agent.py`.

## Current Agents

| Agent | Name | Description |
|-------|------|-------------|
| `ChatAgent` | `"chat"` | General-purpose conversational agent |
| `SummarizeAgent` | `"summarize"` | Condenses conversation history into a summary |
| `ToolAgent` | `"tool"` | Inner tool-execution loop; parses `ToolCall` from LLM output |
| `PlannerAgent` | `"planner"` | Returns structured `ExecutionPlan` (JSON schema injected into prompt) |
| `ReviewerAgent` | `"reviewer"` | Returns structured `ReviewResult` (JSON schema injected into prompt) |

## Agent Contract

```python
class Agent(Protocol):
    name: str  # unique; used for registry lookup and response metadata
    async def handle(self, request: AgentRequest, ctx: AgentContext) -> AgentResponse: ...
```

## Adding an Agent

1. Create `agents/<name>.py`.
2. Define the class satisfying the `Agent` protocol — `name` must be a class-level `str`.
3. If structured output is needed, define a `BaseModel` schema and inject via `build_structured_prompt(Model.model_json_schema())`.
4. Export from `agents/__init__.py`.
5. Register factory in `composition.py`: `agent_registry.register("<name>", lambda cfg: MyAgent(settings=cfg))`.
6. Write `tests/test_<name>.py`.

## System Prompt Injection Pattern

All agents that need a system prompt follow this pattern:
```python
def __init__(self, system_prompt: str = "", ...) -> None:
    self._system_prompt = system_prompt + build_structured_prompt(Schema.model_json_schema())

async def handle(self, request: AgentRequest, ctx: AgentContext) -> AgentResponse:
    messages = list(request.messages)
    if not messages or messages[0].role != "system":
        messages = [Message(role="system", content=self._system_prompt)] + messages
    ...
```

This ensures:
- No duplicate system prompt if caller already injected one.
- Custom prefix is prepended, schema appended.

## Tool-Enabled Agents

`ToolAgent` reads `ctx.tools` (a `ToolRegistry`) to discover available tools.
If `ctx.tools` is `None` or empty, it skips tool-prompt injection and acts as
a plain conversational agent.
