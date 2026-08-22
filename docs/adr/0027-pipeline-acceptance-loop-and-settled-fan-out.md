# ADR-0027: Whole-pipeline acceptance loop + settled fan-out outcomes

## Status

Accepted

## Context

The acceptance loop lives only in single-agent `dispatch`; `dispatch_pipeline`
cannot iterate, so a flow whose final verdict should drive a retry of the whole
pipeline has no expressible form. That is the orchestrator-side prerequisite
for the workflow layer's "composite `loop` bodies" follow-up (NEXT_STEPS): a
`loop` node wrapping a sub-pipeline must compile to one public dispatch call,
and no such call existed. Separately, `dispatch_fan_out` is
`asyncio.gather` without `return_exceptions` — one bad agent discards every
successful sibling — and no partial-results mode exists.

## Decision

`dispatch_pipeline` gains keyword-only optional `acceptance_fn` / `max_steps`
(both `None` → today's single pass, byte-identical). Loop mode treats the
**whole pipeline as one loop body**: the FINAL response is judged; a reject
re-runs the pipeline with the prior final assistant reply appended to the
first-stage messages (`dispatch`'s re-injection idiom); exhaustion raises
`MaxStepsExceeded`. Budget precedence: kwarg > `loop_settings.max_steps` >
field default — `request.max_steps` is excluded because it already governs
each inner dispatch and would otherwise double-apply.

Fan-out partial results ship as an **additive sibling**,
`dispatch_fan_out_settled(...) -> list[FanOutOutcome]` — gather with
`return_exceptions=True`, roster order preserved, `FanOutOutcome` a frozen
dataclass in `core/orchestrator.py` (exactly one of `response`/`error`; `ok`
property). `dispatch_fan_out` stays fail-fast and byte-identical.

## Consequences

### Positive

- Composite workflow `loop` bodies gain their orchestrator primitive: loop a
  sub-pipeline through one public call, acceptance never reimplemented
  (only the graph-side `WorkflowStep`/`SequenceNode` widening remains).
- Callers who need partial fan-out results get typed, ordered outcomes with
  per-agent error visibility (`fan_out.failed_count` span attribute; debug
  log of names + error codes, never content).
- The pipeline's returned response carries the same `"loop"` metadata block
  shape `dispatch` uses, so consumers read one telemetry contract.

### Negative / Trade-offs

- A rejected pipeline pass re-runs **every** stage, not just the failing one —
  the deliberate semantics (the final verdict judges the whole flow), but a
  k-pass loop over m agents costs k·m inner dispatches, each its own metric
  point per ADR-0026.
- Two loop tiers can nest: `loop_settings.max_steps` caps both the pipeline
  loop and each inner dispatch when neither states its own budget. Excluding
  `request.max_steps` from the pipeline tier keeps one value from silently
  applying at both levels, but operators raising `MANGOMAS_LOOP__MAX_STEPS`
  raise both tiers.
- `FanOutOutcome.error` is `BaseException` — settled mode reports rather than
  filters, so callers decide what a non-`MangomasError` failure means.

### Neutral

- No new instruments (inner dispatches already count per-agent — cardinality
  stays stable), no new error types, no new config; `dispatch_pipeline`'s
  span keeps its name and gains loop attributes only in loop mode;
  `orchestrator.dispatch_fan_out_settled` is a new span for a new method.
- Fan-out acceptance is recorded as not-applicable: an `AcceptanceFn` judges
  one response, and acceptance over a list is undefined semantics.

## Alternatives Considered

- **Final-stage-only pipeline loop** — rejected: already composable today by
  calling `dispatch` with `acceptance_fn` on the last agent; adds nothing.
- **Acceptance over fan-out lists** (all/any/quorum) — rejected: no principled
  single meaning; whichever was picked would be wrong for someone. Recorded
  as not-applicable instead.
- **Making `dispatch_fan_out` settle in place** (e.g. a `settled=` flag or a
  union return) — rejected: its fail-fast contract has callers (the workflow
  `fan_out` node), and a return type that changes shape by flag is a trap.
- **`FanOutOutcome` in `agent.py`** — rejected: that file is the wire-DTO
  surface; a topology result belongs beside the topology.

## References

- Code: `src/mangomas/core/orchestrator.py` (`dispatch_pipeline`,
  `_pipeline_pass`, `_pipeline_effective_max_steps`,
  `dispatch_fan_out_settled`, `FanOutOutcome`), `src/mangomas/core/__init__.py`
- Spec: `specs/0027-pipeline-acceptance-and-settled-fan-out.md`
- Related ADRs: ADR-0026 (loop-settings wiring + per-invocation metric
  semantics this builds on), ADR-0018 (the additive-sibling precedent for
  fan-out evolution), ADR-0021 (governance contract)
