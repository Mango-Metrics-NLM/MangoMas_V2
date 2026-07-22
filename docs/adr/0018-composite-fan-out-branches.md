# ADR-0018: Composite fan_out branches

## Status

Accepted

## Context

`FanOutNode.branches` is `list[AgentNode]`, so a `fan_out` can only fan to single
agents — you cannot fan out to two sub-pipelines. The workflow invariant is
"every leaf is one public dispatch call; never reimplement the fan-out gather",
and `fan_out` delegates to `Orchestrator.dispatch_fan_out(names, request)`. But
`dispatch_fan_out` is **name-based**, so a composite child (a `loop` / `branch`
/ nested `fan_out`) cannot be expressed as a name. We need composite branches
without regressing the all-agent case. (`sequence` stays a non-step, so
sub-pipeline fan-out remains a future enhancement — deeper nesting is a separate
decision.)

## Decision

Widen `FanOutNode.branches` to `list[WorkflowStep]` and make the executor
**hybrid**:

- **All-`AgentNode` branches → `dispatch_fan_out(names, request)` verbatim** — the
  parity fast-path, byte-identical in output, metadata, **and spans** (the
  `orchestrator.dispatch_fan_out` span is preserved), honouring the "one dispatch
  call" invariant.
- **Any composite branch → `asyncio.gather(*(resolve_executor(b).run(...)))`** —
  a deliberate, bounded relaxation of "never reimplement the gather", justified
  because `dispatch_fan_out` cannot take a composite child. The `join`
  (`first` / `concat`) reduction is unchanged.

## Consequences

### Positive

- Parallel sub-pipelines / loops / branches under a `fan_out`; every existing
  all-agent graph is byte-identical (parity test) — the widening is a superset,
  so no graph breaks.
- No protected-path edit; `NodeExecutor` unchanged; join semantics preserved.
- Closes the embedding-adapter live-test gap (LM Studio + Vertex, gated).

### Negative / Trade-offs

- The composite path reimplements the gather (the invariant's escape hatch). It
  is scoped to the composite case only; the common all-agent path keeps the
  primitive, so observability (the `dispatch_fan_out` span) is unchanged there.
- A composite branch's inner spans differ from an agent branch's — expected,
  since it runs a nested executor tree.

### Neutral

- `min_length=1` on `branches` is retained. `schema_version` stays `1` (the field
  type is widened, not a new kind).

## Alternatives Considered

- **Always gather over `resolve_executor`** (drop `dispatch_fan_out` entirely) —
  rejected: it would change the all-agent span tree and reimplement the gather
  unconditionally, weakening the invariant for the common case with no benefit.
- **A separate `composite_fan_out` node kind** — rejected: two node kinds for one
  concept; the superset widening keeps one `fan_out` and stays backwards-compatible.

## References

- Code: `src/mangomas/workflow/graph.py` (`FanOutNode.branches`),
  `src/mangomas/workflow/nodes/fan_out.py`, `src/mangomas/core/orchestrator.py`
  (`dispatch_fan_out`).
- Related: ADR-0011 (workflow graphs); ADR-0016 (branch node — the nested-executor
  precedent); spec `specs/0013-composite-fan-out-branches.md`.
