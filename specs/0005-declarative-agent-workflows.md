# Spec-0005: Declarative multi-agent workflow graph

- **Status:** Implemented
- **Linked ADR:** [ADR-0007](../docs/adr/0007-declarative-agent-workflows.md)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added — Declarative multi-agent workflow graph`

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

- [x] A JSON graph of `planner → tool → reviewer` produces the same result as
      the imperative `dispatch_pipeline` call
      (`tests/test_workflow_runner.py::test_linear_graph_matches_dispatch_pipeline`).
- [x] Unknown agent in a graph raises `AgentNotFound` (no new error type needed) —
      validated fail-fast before any dispatch.
- [x] Off by default (`MANGOMAS_WORKFLOW__ENABLED=false`); 95% coverage maintained
      (workflow package at 100%).

## Open questions — resolved

- **Graph format:** JSON object of `nodes` (id, agent, `depends_on` edges,
  optional `until`/`max_steps`). The eval `target` config was *not* reused (it
  models one topology per run and returns a string); the `first`/`concat` join
  vocabulary is shared. See ADR-0007.
- **Conditional edges & per-edge data routing:** deferred. v1 uses
  level-synchronized execution (a node reads the previous level's joined
  output). Conditional/branch edges are a follow-up.
