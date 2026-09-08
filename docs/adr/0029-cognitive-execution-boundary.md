# ADR-0029: Cognitive/execution envelope boundary

## Status

Accepted

## Context

Peer review of integrating
[ianshank/Mango_Code_Agent-Harness](https://github.com/ianshank/Mango_Code_Agent-Harness)
agreed that Mango-Mas V2 is the cognitive plane and the harness is the
authority plane. Merging orchestrators, sharing internals, or letting MoE
confidence select `allowed_tools` would violate harness INV-16. This repo's
`mangomas.harness` is Claude Code governance plus an OTel wrap — not that
execution broker. ADR-0003 already rejected vendoring an external eval
harness.

## Decision

Publish a standalone `mango-integration-contracts` package (schema **1.1.0**,
Pydantic v2, `extra="forbid"`) that both repos depend on. Mango-Mas emits
`CognitiveSignal`; the harness validates, archives, and independently
disposes. Do not import harness `ExecutionBroker` / `command_actions` here,
and do not overload `MANGOMAS_HARNESS__*`.

## Consequences

### Positive

- Blast radius of a hallucinating cognitive plane is bounded by schema
  validation plus the harness PDP.
- INV-16 is CI-enforceable (metamorphic confidence test; nested
  authority-key walker).
- Extractable to its own repo without rewriting models.

### Negative / Trade-offs

- 1.1.0 is not wire-compatible with the harness's dataclass 1.0.0; ingest
  stays blocked until the companion bump.
- `is_prompt_eligible` is a context-compiler predicate and can be misread as
  a permission API — tests pin that it never appears in PDP input.

### Neutral

- Runtime emission from planner/reviewer is default-OFF
  (`MANGOMAS_SIGNAL__ENABLED=false`) via `mangomas.cognitive`. Contracts live
  in `mango-integration-contracts`; the producer must not import harness
  `ExecutionBroker` / `command_actions`.
- Hugging Face MoE / MemoryCell are not this tree; `routing.recommendation`
  is an optional payload for a future producer, not a capability selector.

## Alternatives Considered

- **Advisory sidecar only, no shared schema** — rejected: untyped JSON will
  grow a control field under another name.
- **In-process MoE configures harness tools** — rejected: model-derived
  capability grant (INV-16).
- **Map cognitive `developer` → harness `implementer`** — rejected: same
  grant with extra indirection (architecture-set §4 ROLE_MAP).
- **Put models in `mangomas.cognitive`** — rejected: the harness would then
  import Mango-Mas internals, reversing dependency direction.
- **OPA/Rego in this change** — deferred until capability grants become
  data-driven; keep in-code PDP on the harness side.

## References

- Spec: `specs/0030-cognitive-authority-plugin.md`
- Review: `docs/analysis/20260908-governed-coding-platform-architecture-review.md`
- Code: `mango-integration-contracts/src/mango_contracts/`
- Related ADRs: ADR-0003, ADR-0021
- External: harness `docs/specs/mangomas-integration-core.md`, INV-16
