---
name: mango-topology
description: >
  Multi-agent orchestration topologies in Mango-Mas V2. Use when:
  composing a pipeline (planner → tool → reviewer), fanning out a request
  to multiple agents in parallel, building an iterative acceptance loop,
  adding a streaming endpoint, or diagnosing MaxStepsExceeded. Covers
  Orchestrator.dispatch_pipeline, dispatch_fan_out, stream_dispatch, and
  the AcceptanceFn contract from core.loop.
argument-hint: "Describe the topology (e.g. 'planner → executor → critic loop') or paste a failing topology test"
---

# Mango-Mas Topology Skill

## When to Use

- Compose a sequential agent pipeline (output of A feeds B feeds C)
- Run multiple agents in parallel against the same request (fan-out)
- Add an iterative loop with an acceptance criterion (`AcceptanceFn`)
- Stream tokens through `Orchestrator.stream_dispatch`
- Diagnose `MaxStepsExceeded` or unexpected loop behaviour
- Add a new topology test under `tests/test_topologies.py`

---

## Quick Commands

```powershell
# Topology + streaming tests
python -m pytest tests/test_topologies.py tests/test_streaming.py tests/test_control_loop.py -v

# Full suite
python -m pytest --tb=short -q
```

```bash
python -m pytest tests/test_topologies.py tests/test_streaming.py tests/test_control_loop.py -v
python -m pytest --tb=short -q
```

---

## Topology Rules

| Rule | Detail |
|------|--------|
| Single orchestrator API | All composition goes through `Orchestrator.dispatch*` methods — never call agents directly. |
| Backwards compatible | `dispatch(agent_name, request)` (single agent) remains the simplest path; pipelines/fan-out are opt-in. |
| Pipeline contract | The output `content` of step N is fed as the user message of step N+1 (see `dispatch_pipeline`). |
| Fan-out contract | Each agent receives the **same** original request; returns `list[AgentResponse]` in declaration order. |
| Loop contract | `AcceptanceFn` is a sync `Callable[[AgentResponse], bool]`. Returning `True` stops; reaching `max_steps` raises `MaxStepsExceeded`. |
| Step budget | `LoopSettings.max_steps` caps loop iterations; default is `1` (single shot). |
| Step timeout | `LoopSettings.step_timeout_seconds` caps each step; default is `30.0`. |
| Streaming fallback | If the LLM client doesn't satisfy `StreamingLLMClient`, `_streaming.py` buffers a single chunk + warns. |

---

## Reference

| File | Role |
|------|------|
| `src/mangomas/core/orchestrator.py` | `dispatch`, `dispatch_pipeline` (line 153), `dispatch_fan_out` (line 185), `stream_dispatch` (line 212) |
| `src/mangomas/core/loop.py` | `AcceptanceFn` type alias |
| `src/mangomas/agents/_streaming.py` | Shared streaming-fallback helper |
| `src/mangomas/agents/planner.py` | Structured output for use in pipelines |
| `src/mangomas/agents/reviewer.py` | Acceptance-loop pattern (`ReviewResult.passes`) |
| `tests/test_topologies.py` | Reference pipeline + fan-out tests |
| `tests/test_streaming.py` | Reference streaming tests (including fallback) |
| `tests/test_control_loop.py` | Reference acceptance-loop tests |

---

## Template — Sequential Pipeline

```python
from mangomas.core.agent import AgentRequest, Message

request = AgentRequest(messages=[Message(role="user", content="ship the feature")])
final = await orchestrator.dispatch_pipeline(
    ["planner", "tool", "reviewer"],
    request,
)
# final.content == reviewer's response; intermediate outputs are in final.metadata["pipeline"]
```

## Template — Parallel Fan-out

```python
responses = await orchestrator.dispatch_fan_out(
    ["reviewer", "summarize"],
    request,
)
# responses[0] is reviewer's AgentResponse; responses[1] is summarize's
```

## Template — Iterative Acceptance Loop

```python
from mangomas.core.loop import AcceptanceFn

is_done: AcceptanceFn = lambda r: "DONE" in r.content

final = await orchestrator.dispatch(
    "chat",
    request,
    acceptance_fn=is_done,
    max_steps=5,
)
```

## Template — Streaming

```python
async for token in orchestrator.stream_dispatch("chat", request):
    print(token, end="", flush=True)
```

---

## Workflow

1. Decide which topology fits: single-shot (`dispatch`), pipeline, fan-out, or loop.
2. Confirm all participating agents are registered in `composition.py::agent_registry`.
3. If using an acceptance loop: define a pure `AcceptanceFn` (no I/O) and cap `max_steps`.
4. If streaming: confirm the underlying `LLMClient` is also a `StreamingLLMClient`; otherwise expect a single buffered chunk + warning.
5. Add a test in `tests/test_topologies.py` (or `test_streaming.py`) using `FakeLLM(replies=[...])` to simulate multi-step behaviour.

---

## Constraints

- DO NOT call `agent.execute(ctx, request)` directly — go through `Orchestrator`.
- DO NOT make `AcceptanceFn` async — it must be a sync callable.
- DO NOT exceed `LoopSettings.max_steps` by design — handle `MaxStepsExceeded` explicitly if the caller expects a "best effort" answer.
- DO NOT mutate `request.messages` in place between pipeline stages — the orchestrator threads a new request.
- DO NOT swallow `MaxStepsExceeded` silently — surface it or convert it to a domain-meaningful error.

---

## Diagnosing Failures

1. `MaxStepsExceeded` after one step → `acceptance_fn` returned `False` for the first response and `max_steps=1` (the default); raise `max_steps` or refine the fn.
2. Pipeline outputs look wrong → check that step N's `content` is what step N+1 expects as user input.
3. Fan-out returns out-of-order responses → `dispatch_fan_out` preserves agent declaration order; check the input list.
4. Streaming yields a single big chunk + warning → the LLM client doesn't satisfy `StreamingLLMClient`; either implement `.stream()` on the adapter or accept the fallback.
5. Pipeline coverage gaps → add a test where one step raises `LLMError` to exercise propagation.
