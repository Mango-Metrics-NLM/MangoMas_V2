# ADR-0023: Workflow implementation reconciliation (`main` vs. `feat/initial-release`)

## Status

Accepted

## Context

`main` and `feat/initial-release` diverged and each implements `workflow/`
differently. `main` (11 commits ahead of the merge-base `922b4c3`) carries a DAG
model: `workflow/{models,loader,runner}.py`, where `WorkflowGraph` is an explicit
node/edge list and `WorkflowRunner.execution_levels()` runs a Kahn's-algorithm
topological sort at **runtime**, threading each level's joined output into the next.
`feat/initial-release` (50 commits ahead) carries a bounded-tree model:
`workflow/{graph,predicate,registry,executor,loader}.py` plus a `nodes/` package,
where `WorkflowGraph.root` is a closed, `extra="forbid"` discriminated union of
`agent`/`fan_out`/`loop`/`branch`/`sequence` nodes, resolved by structural recursion
with no scheduler — ADR-0011 states this is deliberate: *"Acyclic by construction
(bounded tree) → no cycle detection"* needed. `main` allocates ADR-0007 for its
version of this decision and ADR-0011 for a harness-hook-hardening layer that does
not exist on `feat/initial-release`, where ADR-0011 is already allocated to the
bounded-tree decision above — a genuine numbering collision, not just a design one.

`feat/initial-release`'s implementation is materially more developed: it ships
conditional `branch` nodes (ADR-0016), composite `fan_out` bodies (ADR-0018), an HTTP
endpoint (ADR-0012), and a CLI surface, none of which exist on `main`. Discarding it
in favor of `main`'s DAG would retire three landed ADRs and two specs for no
capability gain nobody has asked for.

`main`'s one piece of unique, reusable value is `WorkflowGraph.execution_levels()`
— a small, self-contained topological-level compiler that turns an explicit
dependency graph into a sequence of parallel levels. It is described in its own
docstring as *"a thin planner of topologies... no new execution engine is
introduced"* — i.e. it was already designed to compile down to dispatch primitives,
not to run its own scheduler at the leaf level.

## Decision

`feat/initial-release`'s node-registry tree is the executed model going forward.
`main`'s `runner.py` (the class that executes `WorkflowNode`s directly against
`main`'s own model types) is retired — it does not type-check against this branch's
`WorkflowStep`/`WorkflowNode` union and re-adapting it would mean maintaining a
second execution engine inside `workflow/`, which directly contradicts ADR-0011's
bounded-tree invariant (a runtime DAG scheduler is exactly the cycle-capable
execution path ADR-0011 chose not to need).

A future `dag` node kind, if one is ever requested, should absorb only
`execution_levels()`'s topological-sort algorithm (~30 lines) into the **loader**,
compiling an explicit node/edge declaration into a `SequenceNode` of
`FanOutNode`s — one `FanOutNode` per topological level — at load time, and handing
the result to the existing, unmodified executors. This keeps ADR-0011's invariant
literally true (the executed artifact is still a bounded tree; cycle detection lives
in the `ConfigError` boundary the loader already owns) and requires no new executor,
no new registry entry, and no runtime scheduler. This design is recorded here as
this ADR's disposition of the reconciliation; implementing the `dag` kind itself is
out of scope for this ADR and is deferred pending an actual requirement (see
NEXT_STEPS.md).

`main`'s ADR-0007 (its version of declarative workflows) is superseded by this
branch's ADR-0011. `main`'s ADR-0011 (harness-hook-hardening) is superseded by this
branch's ADR-0021 (protected-path governance contract), which supplies the
equivalent decision under a number that does not collide on this line.

## Consequences

### Positive

- No regression to the shipped `branch`/composite-`fan_out`/HTTP/CLI surface.
- The ADR-0011 bounded-tree invariant stays literally enforced — no runtime DAG
  scheduler is introduced.
- The reusable part of `main`'s work (the level-compiler algorithm) is not thrown
  away; it is recorded as the design for a future `dag` node, at roughly 30 lines
  instead of `main`'s 345 lines of `runner.py` + `models.py`.
- ADR number collisions with `main` are resolved and recorded, not silently ignored.

### Negative / Trade-offs

- No DAG capability ships in this PR. If a real per-edge-routing DAG requirement
  emerges later (as opposed to level-synchronized execution, which is what
  `main`'s `runner.py` actually implements — see Alternatives), it needs its own ADR.
- `main`'s 474 lines of workflow tests are not ported; a future `dag` node needs a
  fresh test suite against this branch's model.

### Neutral

- `docs/adr/0006` and `docs/adr/0007` remain unallocated on this line, as documented
  in `specs/README.md`; they are not reused for this reconciliation.

## Alternatives Considered

- **Port `main`'s `WorkflowRunner` as a `dag` node's runtime executor** — rejected:
  it would embed a second scheduler inside `workflow/`, breaking ADR-0011's
  acyclic-by-construction property, and its level-synchronized semantics are not
  actually per-edge DAG semantics (a node receives its *entire previous level's*
  joined output, not only its declared predecessors' output) — shipping that under
  the name `dag` would be a correctness footgun wearing a feature's name.
- **Adopt `main`'s DAG model wholesale, port `branch`/composite-`fan_out` onto it** —
  rejected: retires three landed ADRs (0011, 0016, 0018) and two specs (0012, 0013)
  for a capability (arbitrary DAGs) nobody has requested; `NEXT_STEPS.md` names
  composite `loop` bodies and node-kind discovery as the actual workflow follow-ups.

## References

- Code: `src/mangomas/workflow/graph.py:80-99` (the closed union + acyclic
  invariant), `origin/main:src/mangomas/workflow/models.py:113-148`
  (`execution_levels`), `origin/main:src/mangomas/workflow/runner.py`
- Related ADRs: ADR-0011 (declarative workflows, this line), ADR-0016 (branch node),
  ADR-0018 (composite fan_out); supersedes `main`'s ADR-0007 and ADR-0011
- External: merge-base `922b4c3`; `main` +11 commits / `feat/initial-release` +50
  commits from that point
