# Spec-0005: Declarative multi-agent workflow graph

- **Status:** Implemented — all acceptance criteria met; the "Open questions"
  below were resolved as deliberate deferrals (conditional edges, composite
  `loop`/`fan_out` bodies, third-party node-kind discovery), tracked as Phase
  3 backlog in `docs/analysis/20260822-next-steps-roadmap-analysis.md` and
  `NEXT_STEPS.md`.
- **Linked ADR:** ADR-0011
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added`

## Problem

`NEXT_STEPS.md` › "Long term" › *Multi-agent workflows*: today topologies are
expressed imperatively (`dispatch_pipeline([...])`, `dispatch_fan_out([...])`).
Operators want to compose planner → executor → reviewer graphs **declaratively**
(config/JSON) and have `Orchestrator` execute them, without editing Python.

## Requirements

- A declarative graph definition supporting **sequential + fan-out +
  acceptance-loop** composition (conditional branching is a follow-up).
- Compiled down to the existing `dispatch`, `dispatch_pipeline`,
  `dispatch_fan_out`, and `AcceptanceFn` loop — **no new execution engine**.
- Graph source is env/Settings-driven (a `WorkflowSettings` group), no
  hard-coded topologies.
- Must remain **additive & default-OFF**: the default `MANGOMAS_WORKFLOW__ENABLED
  =false` leaves single-agent `dispatch` and the imperative topology methods
  byte-identical.

## Design (as built)

A workflow is a **bounded tree** with a single `root` node. Composition lives
only in a `sequence`; a `fan_out` fans to agents; a `loop` wraps one agent. Every
leaf therefore maps 1:1 onto one public dispatch call, so the executor never
reimplements pipeline threading, fan-out `gather`, or the acceptance loop. The
tree is acyclic by construction — **no cycle detection is needed** (the stub's
"cycles → typed error" test is therefore unreachable and intentionally omitted).

Node kinds (frozen Pydantic discriminated union, `extra="forbid"`):

| kind | fields | compiles to |
|------|--------|-------------|
| `agent` | `agent: str` | `dispatch(agent, request)` (verbatim) |
| `fan_out` | `branches: list[agent]`, `join: first\|concat` | `dispatch_fan_out(names, request)` + join reduce |
| `loop` | `agent: str`, `accept: PredicateSpec`, `max_steps: int` | `dispatch(agent, acceptance_fn=…, max_steps=…)` |
| `sequence` | `steps: list[agent\|fan_out\|loop]` | threads content+metadata like `dispatch_pipeline` |

Executors are **metadata-transparent** (they return the `dispatch*` result
verbatim; provenance goes to OTel spans), so an all-agent `sequence` is
byte-identical to `dispatch_pipeline`. The `PredicateSpec` (`contains` / `regex`)
compiles once to a pure, synchronous `AcceptanceFn`.

Example graph:

```json
{
  "schema_version": 1,
  "name": "plan-review-refine",
  "root": {
    "kind": "sequence",
    "steps": [
      {"kind": "agent", "agent": "planner"},
      {"kind": "fan_out", "join": "concat",
       "branches": [{"kind": "agent", "agent": "reviewer"},
                    {"kind": "agent", "agent": "summarize"}]},
      {"kind": "loop", "agent": "chat", "max_steps": 5,
       "accept": {"kind": "contains", "value": "DONE", "case_sensitive": false}}
    ]
  }
}
```

## Config / env additions

| Env var (`MANGOMAS_*`) | Default | Purpose |
|------------------------|---------|---------|
| `MANGOMAS_WORKFLOW__ENABLED` | `false` | Enable declarative graph dispatch |
| `MANGOMAS_WORKFLOW__DEFINITION` | _(none)_ | Path to a JSON graph, or inline JSON |

All tunables are `DEFAULT_WORKFLOW_*` module constants surfaced through the
`WorkflowSettings` group — **no hard-coded values**. The graph declares its own
`schema_version` (validated at load).

## Protocol / contract impact

- **New protocols:** `NodeExecutor` (`workflow/executor.py`) — a pure-domain
  seam, not an adapter. No change to the `Agent` protocol.
- **New error types:** _none_ — malformed/cyclic/unknown-kind/unsupported-version
  map to the existing `ConfigError` (400, via the loader boundary and
  `UnknownProvider`); an unknown agent surfaces at execution as `AgentNotFound`
  (404). `errors.py` and `api/app.py::_ERROR_STATUS` are unchanged.
- **Registry additions:** `node_registry` (`workflow/registry.py`, a
  `Registry[NodeExecutorFactory]`) mirroring `eval.target_registry`.

## Backwards-compatibility

- Disabled by default → orchestrator behaviour byte-identical to today.
- `build_orchestrator` signature/return type unchanged; `composition.py`
  untouched; the workflow is resolved lazily at the CLI call site.
- Imperative `dispatch_*` methods remain the primitives the graph compiles to.
- No protected core file (`core/*`, `errors.py`, `registry.py`) is edited.

## Test plan

- Unit: `tests/test_workflow_graph.py` (parse/validation), `test_workflow_predicate.py`
  (predicate compilation), `test_workflow_registry.py`, `test_workflow_settings.py`,
  `test_workflow_loader.py`.
- Parity: `tests/test_workflow_executor.py` proves an all-agent `sequence` equals
  `dispatch_pipeline` (including the `planner → tool → reviewer` shape), `fan_out`
  equals `dispatch_fan_out` + join, and `loop` matches the acceptance loop
  (accept + `MaxStepsExceeded`), plus nesting and unknown-agent paths.
- CLI: `tests/test_workflow_cli.py` (off-by-default exit 2; run/validate).
- Coverage: 95% global gate + a new `workflow` per-package floor in
  `scripts/check_coverage.py`.

## Acceptance criteria

- [x] An all-agent `sequence` (e.g. `planner → tool → reviewer`) produces the
      same result as the imperative `dispatch_pipeline` call.
- [x] Unknown agent in a graph raises `AgentNotFound` (no new error type needed).
- [x] Off by default; 95% coverage maintained (workflow package at 100%).
- [x] `ruff`, `pytest` (95% gate), `frontmatter-lint` clean.

## Open questions — resolved

- **Graph format:** a recursive frozen-Pydantic tree (JSON), not a DSL and not
  the eval `target` config (targets return `str`, so they cannot compose). See
  ADR-0011.
- **Conditional edges:** deferred to a follow-up (needs explicit edges + cycle
  detection), together with composite `loop`/`fan_out` bodies and entry-point
  discovery of third-party node kinds.
