# Spec-0027: Pipeline acceptance threading + settled fan-out (governed Batch B-c)

- **Status:** Implemented
- **Linked ADR:** [ADR-0027](../docs/adr/0027-pipeline-acceptance-loop-and-settled-fan-out.md)
  (whole-pipeline acceptance loop; `FanOutOutcome` + `dispatch_fan_out_settled`
  as the additive sibling of the fail-fast fan-out)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added`/`Changed` (when released)
- **Origin:** `docs/analysis/20260822-next-steps-roadmap-analysis.md` §3
  Phase 1, item 1.2(c) — "`acceptance_fn`/`max_steps` threading through
  pipeline/fan-out and a fan-out partial-results mode (`dispatch_fan_out` is
  `asyncio.gather` without `return_exceptions` — one bad agent discards
  successful siblings)"

## Problem

Two gaps on the topology surface. First, the acceptance loop exists only for
single-agent dispatch: `dispatch_pipeline` cannot iterate at all, so a
plan→execute→review flow whose *final* verdict should drive a retry of the
*whole* flow has no expressible form — the exact shape a composite workflow
`loop` body needs (NEXT_STEPS "composite `loop` bodies": the graph-side blocker
is `WorkflowStep` excluding `SequenceNode`, but the orchestrator-side primitive
did not exist either). A final-stage-only loop is deliberately **not** the gap:
that is already composable by calling `dispatch` with `acceptance_fn` on the
last agent. Second, `dispatch_fan_out` fails fast — one bad agent discards
every successful sibling — and no partial-results mode exists for callers who
want per-agent outcomes.

This is protected-path work: the governance contract is path-based, so any
touch of `core/orchestrator.py` requires a `BREAKING-CHANGE` trailer even
where behaviourally additive.

## Requirements

### R1 — Whole-pipeline acceptance loop

- `dispatch_pipeline` gains **keyword-only, optional**
  `acceptance_fn: AcceptanceFn | None = None` and `max_steps: int | None = None`.
  Both defaulting to `None` preserves today's single-pass behaviour
  **byte-identically** (same span shape, same returned response, no
  pipeline-level metadata rewrite).
- **Semantics** (whole-pipeline, judged on the FINAL response): when
  `acceptance_fn` is provided, run the entire pipeline as one loop body; judge
  the final stage's response; on reject, re-run the whole pipeline with the
  original first-stage request plus the prior **final** assistant reply
  appended to its messages (mirroring `dispatch`'s re-injection idiom); raise
  `MaxStepsExceeded` when the budget is exhausted. Rationale: this is what
  Phase 3's composite `loop` bodies need and cannot be expressed today,
  whereas a final-stage-only loop is already composable via `dispatch`.
- When only `max_steps` is provided (no acceptance): iterate with
  conversational re-injection and return the last response without raising —
  mirroring `dispatch`'s no-acceptance multi-step semantics.
- **Budget precedence** (spec-0027): the `max_steps` kwarg >
  `loop_settings.max_steps` > the `AgentRequest` field default (1).
  `request.max_steps` is deliberately **absent** from this chain: it already
  governs each inner dispatch's own budget (spec-0026 R2), and reading it at
  the pipeline tier too would double-apply one value at two loop levels.
  `max_steps < 1` raises `ValueError`, mirroring `dispatch`.
- **Loop telemetry**: in loop mode the returned response's metadata carries
  the same `"loop"` block shape `dispatch` uses
  (`{"steps_taken", "accepted"}`) at the **pipeline** level (the block takes
  the `loop` key on the returned response); the inner dispatches' own
  responses — as persisted and as observed by intermediate stages — keep
  their metadata untouched. The `orchestrator.dispatch_pipeline` span keeps
  its name and gains the additive `loop.max_steps` / `loop.steps_taken` /
  `loop.accepted` attributes in loop mode only.
- **Metrics**: no pipeline-level instrument. Each inner hop is a full
  `dispatch` and therefore already its own invocation/error/duration point
  (ADR-0026); a separate pipeline instrument would grow instrument
  cardinality without adding information — cardinality stays stable.
- **Fan-out acceptance is NOT threaded** — recorded as not-applicable rather
  than invented: an acceptance function judges *one* `AgentResponse`, and
  acceptance over a list (all? any? quorum?) is undefined semantics this spec
  declines to define.

### R2 — Typed fan-out partial results

- New frozen dataclass `FanOutOutcome` in `core/orchestrator.py` — **not**
  `agent.py`: it is an orchestrator-topology result, not a wire DTO, so the
  `AgentRequest`/`AgentResponse` surface stays untouched. Fields
  `agent: str`, `response: AgentResponse | None`, `error: BaseException | None`;
  a `__post_init__` guard enforces exactly-one-of response/error; convenience
  property `ok`. Exported from the `mangomas.core` facade (which already
  surfaces `Orchestrator`).
- New additive public method
  `async def dispatch_fan_out_settled(self, agent_names: list[str], request: AgentRequest) -> list[FanOutOutcome]`:
  `asyncio.gather(..., return_exceptions=True)` mapped to outcomes in roster
  order (gather preserves declaration order); `ValueError` on an empty roster,
  mirroring the sibling. Span `orchestrator.dispatch_fan_out_settled` mirrors
  the sibling's attributes (`topology="fan_out"`, `agent_count`) plus the
  additive `fan_out.failed_count`. A debug log records per-agent outcomes —
  agent names + error codes (`MangomasError.code`, else the class name) only,
  never response content.
- `dispatch_fan_out` itself stays **fail-fast and byte-identical**: its
  established contract (any exception propagates immediately, siblings
  discarded) has callers — the workflow `fan_out` node relies on it — so
  settled mode ships as the additive sibling rather than a behaviour change.
- **Metrics**: none of its own — each inner hop is a full `dispatch`, so the
  failed agents' error metrics are already recorded per-agent (ADR-0026).

## Scenarios (WHEN/THEN)

- WHEN `dispatch_pipeline(names, request)` is called with no loop kwargs THEN
  behaviour is byte-identical to pre-spec-0027 (regression pin: the returned
  response equals the final inner dispatch's response verbatim).
- WHEN acceptance accepts on pass *k* THEN the whole pipeline ran *k* times
  (fake call counts) and the returned metadata's `loop` block reads
  `{"steps_taken": k, "accepted": true}`. Guard can fire: judging the first
  stage's response instead of the final one fails this test
  (`mango-mutation-proof` #1).
- WHEN acceptance never passes within the budget THEN `MaxStepsExceeded`
  (422 via the unchanged envelope) carries the effective budget.
- WHEN iteration *n+1* runs THEN its first-stage request carries the prior
  pass's **final** assistant reply — and never an intermediate stage's reply.
  Guard can fire: skipping the append fails the re-injection test (#3).
- WHEN only `max_steps` is provided THEN the pipeline iterates and returns the
  last response without raising.
- WHEN neither the kwarg nor `loop_settings` is present and acceptance rejects
  THEN the budget is the field default (1) — the documented first diagnosis
  for a `MaxStepsExceeded` on the first step.
- WHEN a settled fan-out has mixed outcomes THEN results are index-aligned
  with the roster, each outcome carries exactly one of response/error, and
  `fan_out.failed_count` counts the failures. Guard can fire: dropping
  `return_exceptions=True` fails the mixed-outcome test (#2).
- WHEN `dispatch_fan_out` (fail-fast) meets one bad agent THEN it still raises
  and discards siblings (regression pin).

## Config / env additions

_None._ `MANGOMAS_LOOP__MAX_STEPS` participates in the new pipeline budget
chain via the already-wired `loop_settings` (spec-0026); no new tunable.

## Protocol / contract impact

- New/changed protocols: _none_. `dispatch_pipeline`'s positional argument
  names are unchanged; the two new parameters are keyword-only optional.
  `dispatch_fan_out_settled` and `FanOutOutcome` are additive.
- New error types: _none_ (`MaxStepsExceeded` / `ValueError` reuse).
- Registry additions: _none_.
- **Governance**: the commit carries a `BREAKING-CHANGE` trailer (path-based
  obligation: `core/orchestrator.py`), landing as governed Batch B-c with
  companion ADR-0027.

## Backwards-compatibility

- `dispatch(name, request)` and every existing topology call with no new
  kwargs behave identically; `dispatch_fan_out` is untouched.
- Existing OTel span names unchanged; `orchestrator.dispatch_pipeline` gains
  loop attributes only in loop mode; `orchestrator.dispatch_fan_out_settled`
  is a new span for a new method.
- `AcceptanceFn` stays sync; the pipeline loop calls it inline exactly as
  `dispatch` does.
- Instrument names/labels/counts unchanged for existing call shapes; a
  pipeline loop of *k* passes over *m* agents emits *k·m* inner points — the
  ADR-0026 per-invocation semantics applied verbatim, not a new rule.

## Test plan

- Topologies (`tests/test_topologies.py`): accepted-on-step-k (call counts +
  loop metadata); exhaustion → `MaxStepsExceeded`; defaults-None single-pass
  byte-identical pin (`model_dump` equality); max_steps-without-acceptance
  iteration + conversational re-injection; `max_steps=1` loop-mode single
  pass; `max_steps=0` → `ValueError`; `loop_settings.max_steps` tier; field-
  default tier; final-reply re-injection content assertion. Settled fan-out:
  mixed ordering, all-success, all-fail (incl. a non-`MangomasError`),
  exactly-one-of invariant, empty roster, fail-fast sibling pin, span
  `fan_out.failed_count`.
- Metrics (`tests/test_metrics.py`): settled fan-out counts each inner step
  per-agent, including the failed agent's error point.
- Mutation proofs (run + reported, not committed): (1) judge the first
  stage's response instead of the final → acceptance test fails; (2) drop
  `return_exceptions=True` → mixed-outcome test fails; (3) skip the
  re-injection append → re-injection test fails.
- Coverage: `core/**` stays at its 100 % floor.

## Acceptance criteria

- [x] Whole-pipeline acceptance loop live: judged on the final response,
  re-injection of the prior final reply, `MaxStepsExceeded` on exhaustion,
  pipeline-level `loop` metadata block; defaults-None byte-identical pin
  green.
- [x] Budget precedence kwarg > `loop_settings.max_steps` > field default,
  proven per tier; `request.max_steps` exclusion recorded with rationale.
- [x] `FanOutOutcome` + `dispatch_fan_out_settled` live: order preserved,
  exactly-one-of invariant guarded, empty-roster `ValueError`, span
  `fan_out.failed_count`, per-agent inner metrics (including failures).
- [x] `dispatch_fan_out` fail-fast contract untouched (regression pin).
- [x] No public signature broke (positional names unchanged; new params
  keyword-only optional; new method/type additive; `mangomas.core` facade
  exports `FanOutOutcome`).
- [x] `BREAKING-CHANGE` trailer present (coordinator's commit); ADR-0027
  authored and Accepted.
- [x] `ruff`, `mypy`, `pytest` (95 % gate + per-package floors) all clean;
  three mutation proofs fired and restored.
- [ ] CHANGELOG updated (coordinator's).
