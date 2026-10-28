# ADR-0042: Decision records are a third stream, separate from turns and cognitive signals

## Status

Proposed

## Context

Spec-0044 introduces a (state digest, action, predicted/observed outcome)
record so that configuration choices and their results can be compared
later. Two existing stores could plausibly hold it: the `turns` table
(`TurnRepository`, about to gain identity columns under spec-0041) and the
`CognitiveSignal` 1.1.0 envelope and its JSONL/HTTP sink (spec-0030,
ADR-0029). The record also needs an attachment point on the agent
context, and `core/agent.py` is a protected path whose field set is a
public contract.

## Decision

Decision records are their own package (`mangomas.decisions`), their own
Pydantic model, and their own sink protocol attached on
`AgentContext.extras["decision_sink"]` — the extras-hung pattern spec-0030
established — with no change to `AgentContext`'s fields, no shared table
with turns, and no reuse of the `CognitiveSignal` envelope. The record
stores a digest of state, never the state. Records are append-only;
an outcome is a second record citing the first, never an update.

## Consequences

### Positive

- `core/agent.py` and the orchestrator client are untouched; the change
  needs no `BREAKING-CHANGE` trailer and does not collide with the
  serialised B2 → B3 → B1 orchestrator work.
- `CognitiveSignal`'s authority boundary (INV-16) stays exact: a signal is
  a proposal with a policy binding; a decision record is a measurement
  with no policy semantics. Keeping them apart means neither can be read
  as the other.
- Tenancy partitioning is enforced at the sink, so a record cannot cross
  tenants even if the caller is wrong.

### Negative / Trade-offs

- A third JSONL stream on disk beside `signals.jsonl` and the turn store;
  operators have one more directory to rotate.
- The `predicted` half has no emitter until the orchestrator-dispatch hook
  lands (plan milestone 2.2), so for one PR the stream carries only
  `observed` records from the eval runner.

### Neutral

- Nothing reads the records. A reader (shadow-mode routing, offline
  analysis) is a separate spec and must cite this one.

## Alternatives Considered

- **Alt 1:** extend the `turns` table — rejected because a turn is a
  conversation artefact with user content, and a decision record must not
  carry content; also, spec-0041 is changing that table's identity model
  concurrently.
- **Alt 2:** a new `signal_type` on `CognitiveSignal` — rejected because
  the envelope carries policy binding and TTL semantics that do not apply
  to a measurement, and ADR-0029 fixes the envelope's meaning as
  "cognition proposes".
- **Alt 3:** a new `AgentContext.decision_sink` field — rejected because
  it edits a protected public contract for a default-off feature; extras
  is the established seam.

## References

- Code: `src/mangomas/cognitive/sink.py` (`CognitiveSignalSink` on
  extras), `src/mangomas/cognitive/pdp.py` (INV-16 adapter),
  `src/mangomas/tenancy.py`
- Specs: spec-0044, spec-0030, spec-0041 (reserved)
- Related ADRs: ADR-0017 (tenancy), ADR-0029 (cognitive boundary),
  ADR-0033 (boundary honesty)
