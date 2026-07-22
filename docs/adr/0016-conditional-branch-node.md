# ADR-0016: Conditional branch node

## Status

Accepted

## Context

The workflow graph (ADR-0011) can sequence / fan out / loop but cannot route on
content, blocking the `planner → route → specialised agent` pattern. We want
conditional selection without breaking the bounded-tree, acyclic-by-construction
invariant and without editing protected core. The open question is *what the
routing predicate evaluates against*, since `compile_predicate` operates on an
`AgentResponse`.

## Decision

Add a frozen `BranchNode` (`kind="branch"`) — an ordered list of
`{when: PredicateSpec, then: WorkflowStep}` cases plus an optional `default` — to
the `WorkflowStep` and `WorkflowNode` unions. Its executor compiles each `when`
once (reusing `compile_predicate`) and evaluates them against a synthetic
`AgentResponse` built from the node's **input content** (the last threaded
message); the first match's `then` runs via `resolve_executor`. No match and no
`default` raises `ConfigError`. The node selects exactly one child and adds no
back-edge, so the tree stays acyclic.

## Consequences

### Positive

- Enables content routing (planner → route → agent) as pure composition of shipped
  parts (`PredicateSpec` / `compile_predicate` / `resolve_executor`); a branch
  child may itself be any node kind, including another `branch`.
- Additive union member — existing graphs never carry `kind="branch"`, so they
  validate and run byte-identically; no protected-path edit; `schema_version`
  stays `1`.

### Negative / Trade-offs

- The predicate routes on the **input** content (the last message threaded in),
  not on a fresh probe dispatch — so routing after a planner requires the planner
  to be the prior `sequence` step whose output becomes the branch's input. This
  keeps the node pure/sync (no extra LLM call to decide the route).
- No-match + no-`default` is a `ConfigError` (400), treating an inexhaustive
  branch as a graph-design error rather than silently passing through.

### Neutral

- Cases are evaluated in declared order; first match wins (documented).

## Alternatives Considered

- **Probe-dispatch to decide the route** — rejected: adds an LLM call and makes
  the node non-pure; routing on threaded content covers the planner pattern.
- **Graph edges / a DAG** — rejected (as in ADR-0011): needs cycle detection; a
  tree node that selects one child preserves the acyclic invariant.
- **Require `default` (no error path)** — rejected: an optional `default` with a
  `ConfigError` on inexhaustive routing surfaces graph bugs instead of hiding them.

## References

- Code: `src/mangomas/workflow/graph.py` (`BranchCase`, `BranchNode`, unions),
  `src/mangomas/workflow/nodes/branch.py`, `src/mangomas/workflow/predicate.py`.
- Related: ADR-0011 (workflow graphs); spec `specs/0012-conditional-branch-node.md`.
