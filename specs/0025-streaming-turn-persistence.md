# Spec-0025: Streaming turn persistence + metrics (governed Batch B-a)

- **Status:** Draft
- **Linked ADR:** ADR-0025 _(to be authored with the implementation — required:
  this touches the `core/orchestrator.py` boundary, and ADR-0013 explicitly
  defers orchestrator-level metric emission "until an ADR blesses it")_
- **Linked CHANGELOG entry:** `[Unreleased]` › `Fixed` (when implemented)
- **Origin:** `docs/analysis/20260822-next-steps-roadmap-analysis.md` §3
  Phase 1, item 1.2(a)

## Problem

`Orchestrator.dispatch` persists each turn via `ctx.repo.save_turn(...)`;
`stream_dispatch` never touches `ctx.repo` and the `/agents/{name}/stream`
route records no metrics. Every SSE conversation is therefore invisible to
`GET /history`, to `SummarizeAgent`'s history window, to tenancy-scoped
storage, and to the agent invocation/error/duration instruments. Additionally,
non-streaming agents (`ToolAgent`, `SummarizeAgent`) are silently degraded to a
single unlabelled chunk with no warning and no metadata channel, so a streaming
consumer cannot tell degraded output from real streaming.

This is protected-path work: the governance contract is **path-based, not
signature-based** — any change to `core/orchestrator.py` requires a
`BREAKING-CHANGE` trailer even when the change is behaviourally additive.

## Requirements

- `stream_dispatch` persists the fully-drained turn (accumulate chunks;
  `save_turn` on successful drain), giving parity with `dispatch`'s
  persistence, including tenancy scoping.
- Persistence semantics on early consumer abandonment and upstream error are
  specified explicitly (recommendation: persist only on full drain; record the
  chosen rule in ADR-0025).
- The streaming HTTP route records the same invocation/error/duration metrics
  as the invoke route.
- The SSE framing gains a metadata/terminal event (agent name, degraded-mode
  flag, loop/tool metadata) so silent single-chunk degradation becomes
  observable; the degradation path logs a warning, mirroring the LLM-level
  fallback.
- Signatures of `dispatch`, `stream_dispatch`, `dispatch_pipeline`,
  `dispatch_fan_out` are **unchanged** (protected-contract preservation).
- Must remain **additive & default-OFF** where new framing could affect
  existing consumers: existing token-chunk SSE events are byte-identical; new
  metadata arrives as a distinct event type existing clients can ignore.

## Scenarios (WHEN/THEN)

- WHEN a streamed conversation drains fully THEN a turn row exists and
  `GET /history` returns it (guard can fire: removing the `save_turn` call
  fails the test — `mango-mutation-proof`).
- WHEN a stream errors upstream or is abandoned mid-drain THEN persistence
  follows the ADR-0025 rule and the error metric increments.
- WHEN a non-streaming agent is streamed THEN the terminal metadata event
  carries the degraded-mode flag AND a warning is logged.
- WHEN the feature ships THEN existing SSE token events are unchanged
  (snapshot/parity test — never vacuously green).

## Config / env additions

_None planned._ If the abandonment rule needs tuning, it enters as a
`DEFAULT_*`-backed `Settings` field per convention.

## Protocol / contract impact

- New/changed protocols: _none_ (all signatures preserved).
- New error types: _none expected_; any addition follows the three-file
  lock-step (`errors.py` + `_ERROR_STATUS` + `tests/test_errors.py`).
- Registry additions: _none_.
- **Governance**: the commit series carries a `BREAKING-CHANGE` trailer
  (path-based obligation), lands as its own PR per the small-governed-batch
  rule, and requires the companion ADR-0025.

## Backwards-compatibility

- With no client changes, existing SSE consumers see identical token events;
  the metadata event is additive.
- `dispatch` behaviour is untouched; repositories see the same `save_turn`
  contract they already implement.

## Test plan

- Unit: orchestrator stream-persistence tests against `FakeRepository` +
  `FakeLLM` (`tests/fakes.py`); route-level metrics tests mirroring the invoke
  route's; SSE framing snapshot for the unchanged token path.
- Both directions per Scenarios; mutation-proof the persistence guard.
- Coverage: `core/**` stays at its 100 % floor.

## Acceptance criteria

- [ ] Streamed turns appear in `/history` (tenancy-scoped), proven by test.
- [ ] Streaming metrics parity with invoke, proven by test.
- [ ] Degraded-mode streaming is observable (metadata event + warning log).
- [ ] Existing SSE token framing byte-identical (snapshot test).
- [ ] `BREAKING-CHANGE` trailer present; ADR-0025 authored and Accepted.
- [ ] `ruff`, `mypy`, `pytest` (95 % gate), `frontmatter-lint` all clean.
- [ ] CHANGELOG updated.
