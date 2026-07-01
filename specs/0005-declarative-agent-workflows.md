# Spec-0005: Declarative multi-agent workflow graph

- **Status:** Draft (stub — no code this pass)
- **Linked ADR:** ADR-0007 (to be authored when implementation begins)
- **Linked CHANGELOG entry:** _pending_

## Problem

`NEXT_STEPS.md` › "Long term" › *Multi-agent workflows*: today topologies are
expressed imperatively (`dispatch_pipeline([...])`, `dispatch_fan_out([...])`).
Operators want to compose planner → executor → reviewer graphs **declaratively**
(config/JSON) and have `Orchestrator` execute them, without editing Python.

## Requirements

- A declarative graph definition (nodes = agent names, edges = data flow;
  supports sequential + fan-out + acceptance-loop composition).
- Consumed by `Orchestrator`; **reuses** existing `dispatch_pipeline`,
  `dispatch_fan_out`, and the `AcceptanceFn` loop — no new execution engine.
- Graph source is env/Settings-driven (a `WorkflowSettings` group), no
  hard-coded topologies.
- Must remain **additive & default-OFF**: existing single-agent `dispatch` and
  the imperative topology methods are unchanged.

## Config / env additions (sketch)

| Env var | Default | Purpose |
|---------|---------|---------|
| `MANGOMAS_WORKFLOW__ENABLED` | `false` | Enable declarative graph dispatch |
| `MANGOMAS_WORKFLOW__DEFINITION` | _(none)_ | Path/inline graph definition |

## Protocol / contract impact

- No change to the `Agent` protocol. New `WorkflowGraph` domain model
  (frozen dataclass / Pydantic) parsed into calls against existing dispatch
  methods. Candidate home: `core/` (pure domain) + a thin `agents/`-agnostic
  planner-of-topologies.

## Backwards-compatibility

- Disabled by default → orchestrator behaviour byte-identical to today.
- Imperative `dispatch_*` methods remain the primitives the graph compiles to.

## Test plan

- Graph parse/validation unit tests (cycles, unknown agent → typed error).
- Execution tests using `FakeLLM` agents proving sequential + fan-out semantics
  match the imperative equivalents.

## Acceptance criteria

- [ ] A JSON graph of `planner → tool → reviewer` produces the same result as
      the imperative `dispatch_pipeline` call.
- [ ] Unknown agent in a graph raises `AgentNotFound` (no new error type needed).
- [ ] Off by default; 95% coverage maintained.

## Open questions

- Graph format: JSON vs. a small DSL vs. existing eval `target` config reuse?
- Do conditional edges (branch on acceptance) belong in v1 or a follow-up?
