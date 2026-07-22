# Spec-0012: Conditional branch node

- **Status:** Implemented
- **Linked ADR:** ADR-0016
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added`

## Problem

The declarative workflow graph (spec 0005) can sequence, fan out, and loop, but
cannot **route** — there is no way to pick one of several paths based on the
content threaded into a node. This blocks the canonical `planner → route by plan
type → specialised agent` pattern. This spec adds a `branch` node that selects
exactly one child by predicate, staying an acyclic tree node (no back-edge) so
the graph's bounded-tree invariant holds.

## Requirements

- A frozen `BranchNode` (`kind="branch"`) with an ordered list of cases
  (`{when: PredicateSpec, then: WorkflowStep}`) and an optional `default` step.
- The executor evaluates each compiled `when` predicate against the node's
  **input content** (the last threaded message) and runs the first match's `then`
  via `resolve_executor` (so a branch child may itself be an `agent` / `fan_out` /
  `loop` / `sequence` / `branch`). No match + no `default` → `ConfigError`.
- `BranchNode` is valid both as a `sequence` step and as a graph root.
- Must remain **additive & default-OFF**: existing graphs never carry
  `kind="branch"`, so they validate and run byte-identically; `schema_version`
  stays `1` (the node kind is additive within v1).

## Config / env additions

None (graph-declared JSON). No new `Settings`.

## Protocol / contract impact

- New/changed protocols: _none_ (`NodeExecutor` unchanged; the executor reuses
  `compile_predicate` + `resolve_executor`).
- New error types: _none_ (reuses `ConfigError`, HTTP 400).
- Registry additions: `node_registry.register("branch", …)` in a new
  `workflow/nodes/branch.py`.
- New frozen models `BranchCase` / `BranchNode` in `workflow/graph.py`, added to
  the `WorkflowStep` and `WorkflowNode` discriminated unions.

## Backwards-compatibility

- Additive union member; no protected-path edit; `dispatch*` primitives unchanged.
- An all-`agent` sequence is still byte-identical to `dispatch_pipeline`.

## Test plan

- Unit (`tests/test_workflow_branch.py`, workflow floor 95): first-match wins,
  later cases skipped, `default` fallthrough, no-match+no-default → `ConfigError`,
  a branch nested inside a `sequence`, and a branch whose `then` is a `fan_out`.
- Node-kind constant updated in `tests/constants.py`.
- Coverage: maintain the 95% global gate and the workflow floor.

## Acceptance criteria

- [x] First matching case runs; non-matching cases are skipped (test proves it).
- [x] `default` runs when no case matches; no-match + no-default → `ConfigError`.
- [x] Existing (branch-free) graphs validate + run unchanged.
- [x] `ruff`, `mypy`, `pytest` (95% gate), `frontmatter-lint` all clean.
- [x] CHANGELOG updated; ADR-0016 added.
