# ADR-0007: Declarative multi-agent workflow graphs

## Status

Accepted

## Context

Multi-agent topologies are today expressed imperatively — a caller writes
`dispatch_pipeline([...])` / `dispatch_fan_out([...])` in Python. Operators want
to compose planner → tool → reviewer graphs **declaratively** (config/JSON) and
have the platform execute them without editing code (spec 0005). We already have
the three execution primitives (`dispatch` with an `AcceptanceFn` loop,
`dispatch_pipeline`, `dispatch_fan_out`); the gap is a declarative surface and a
compiler, not a new execution engine.

## Decision

Add a new top-level `mangomas.workflow` package (not `core/`) holding a pure
`WorkflowGraph`/`WorkflowNode` domain model and a thin `WorkflowRunner` that
compiles the graph's **topological levels** (Kahn's algorithm) onto the existing
orchestrator primitives: a single-node level is a `dispatch` (with optional
`until`/`max_steps` acceptance loop), a multi-node level is a `dispatch_fan_out`
joined by `first`/`concat`, and levels thread output→input exactly as
`dispatch_pipeline` does. Enablement is env-driven via `WorkflowSettings`
(`MANGOMAS_WORKFLOW__ENABLED` / `__DEFINITION`), default-OFF. No core, protocol,
or error-type changes.

## Consequences

### Positive

- A linear graph reproduces `dispatch_pipeline` byte-for-byte (proven by test),
  so the declarative path is a faithful superset of the imperative one.
- Zero changes to any protected path (`core/`, `errors.py`): the runner composes
  the orchestrator's public methods, so the stable contract is untouched.
- Structural validation (unique ids, dangling/self edges, cycles) happens at
  graph construction and unknown-agent validation happens fail-fast before any
  dispatch — a malformed graph can never partially execute.

### Negative / Trade-offs

- v1 uses **level-synchronized** execution: a node receives the joined output of
  the *previous level*, not of its specific edge-predecessors. Per-edge data
  routing and conditional edges are deliberate follow-ups.

### Neutral

- The fan-out default join is `concat` (gather-then-synthesize), differing from
  the eval `fan_out` target's `first` default (which scores one prediction); the
  vocabulary (`first`/`concat`) is shared.

## Alternatives Considered

- **Add a `dispatch_workflow` method to `Orchestrator`** — rejected: it would
  edit a protected path and couple the graph compiler to core; a separate
  package that consumes the public API keeps the contract stable.
- **Reuse the eval `target` config as the graph format** — rejected for v1: eval
  targets model a single topology per run and return a string; a workflow
  composes topologies and returns a full `AgentResponse`. A dedicated model is
  clearer (spec 0005 open question).
- **A general DAG engine with per-edge routing** — deferred: level-synchronized
  execution already covers sequential + fan-out + acceptance-loop composition at
  a fraction of the complexity.

## References

- Code: `src/mangomas/workflow/models.py` (`WorkflowGraph`, `WorkflowNode`,
  `execution_levels`), `workflow/runner.py` (`WorkflowRunner`),
  `workflow/loader.py` (`graph_from_settings`, `parse_graph`),
  `config.py` (`WorkflowSettings`), `cli/main.py` (`workflow` command).
- Spec: `specs/0005-declarative-agent-workflows.md`.
- Related ADRs: ADR-0004 (eval target/source indirection — shared join
  vocabulary), ADR-0008 (dynamic agent loading — same env-driven, default-OFF
  discipline).
