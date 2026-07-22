# Spec-0013: Composite fan_out branches

- **Status:** Implemented
- **Linked ADR:** ADR-0018
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added`

## Problem

A `fan_out` node can only fan to single **agents** (`branches: list[AgentNode]`),
so you cannot run two sub-pipelines (e.g. `planner→reviewer` ‖ `summarize`) in
parallel. This spec widens a `fan_out` branch to any `WorkflowStep`, while keeping
an all-`agent` fan_out **byte-identical** to today. Also closes a coverage gap:
the LM Studio + Vertex embedding adapters have no gated live test.

## Requirements

- `FanOutNode.branches` widens from `list[AgentNode]` to `list[WorkflowStep]`
  (an `agent` / `fan_out` / `loop` / `branch` — `sequence` remains a
  non-step, so sub-pipeline fan-out stays a future enhancement).
- **Parity property (all-`agent`):** when every branch is an `AgentNode`, the
  executor still delegates to `Orchestrator.dispatch_fan_out(names, request)` —
  identical output, metadata, **and spans**. Only when a branch is composite does
  it `asyncio.gather` over `resolve_executor(branch).run(...)` (dispatch_fan_out
  is name-based and cannot express a composite child).
- The `join` (`first` / `concat`) semantics are unchanged.
- Must remain **additive & backwards-compatible**: every existing
  `list[AgentNode]` graph still validates (an `AgentNode` **is** a `WorkflowStep`)
  and runs byte-identically.
- Add gated live embedding smoke tests for `LMStudioEmbeddingClient`
  (`RUN_LMSTUDIO=1`) and `VertexEmbeddingClient` (`RUN_VERTEX=1`).

## Config / env additions

None. Gated tests reuse `RUN_LMSTUDIO` / `RUN_VERTEX`.

## Protocol / contract impact

- New/changed protocols: _none_ (`NodeExecutor` unchanged).
- New error types: _none_.
- `FanOutNode.branches` type widened (a superset — backwards-compatible).

## Backwards-compatibility

- Type widening is a **superset**; all-`agent` fan_out delegates to
  `dispatch_fan_out` unchanged (parity). No protected-path edit.

## Test plan

- Unit (`tests/test_workflow_fan_out_composite.py`, workflow floor 95): **parity**
  (all-agent fan_out output equals the pre-change `dispatch_fan_out` path,
  golden assertion); composite branch that is a `loop` / `branch`;
  `join` first/concat over composite branches.
- Gated live (`tests/lmstudio/test_embeddings.py` `RUN_LMSTUDIO=1`; a Vertex
  embeddings case under `tests/vertex/` `RUN_VERTEX=1`): `embed` / `embed_batch`
  return finite vectors of the expected shape.
- Coverage: maintain the 95% global gate + workflow/adapters floors.

## Acceptance criteria

- [x] All-agent fan_out is byte-identical to today (parity test).
- [x] A fan_out branch may be a `sequence` / `loop` / `branch` and runs correctly.
- [x] Existing graphs validate + run unchanged.
- [x] Gated embedding smoke tests added (skipped by default).
- [x] `ruff`, `mypy`, `pytest` (95% gate), `frontmatter-lint` clean; CHANGELOG + ADR-0018.
