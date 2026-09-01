# ADR-0028: Per-Agent Model Override via `AgentContext.extras`

## Status

Accepted.

## Context

`AgentSettings.model_override: str | None` (`src/mangomas/config/agents.py`)
has existed since spec-0014 but was documented as "Reserved — not read by any
agent yet," because activating it needs a composition-layer change: today
exactly one shared `LLMClient` is built once in
`composition/builder.py::build_orchestrator` and handed to every agent via one
shared `AgentContext`. Activating `model_override` means constructing extra
per-agent `LLMClient` instances and giving agents a way to reach the one that
matches their name — a composition-root change per `specs/README.md`'s
ADR-trigger rule. Full requirements live in
`specs/0028-per-agent-model-override.md`.

## Decision

1. **Seam:** carry per-agent override clients in
   `ctx.extras["agent_llm_overrides"]: dict[str, LLMClient]` — `extras` is
   already documented in `src/mangomas/core/CLAUDE.md` as "the sanctioned
   escape hatch for per-request data that does not deserve a field," and this
   is its first real consumer. A new typed `AgentContext` field was rejected:
   it would touch the protected `core/agent.py` for exactly the shape of need
   `extras` exists to cover.
2. **Cleanup without touching a protected file:** add
   `_AgentLLMOverrideCloseMixin` in `composition/llm.py`, extending
   `_close_hooks()` (`src/mangomas/core/orchestrator.py`) to also close every
   `hasattr(client, "aclose")` value in the overrides dict, with the same
   per-hook fault isolation `Orchestrator.aclose()` already gives its other
   hooks. `composition/harness.py::_HarnessOrchestrator(Orchestrator)` already
   proves that overriding a single method picked up by ordinary Python method
   dispatch requires zero edits to `core/orchestrator.py`; this mixin reuses
   that precedent. `_HarnessOrchestrator` picks up the mixin, and
   `composition/builder.py`'s harness-disabled branch gets a small
   `_Orchestrator(_AgentLLMOverrideCloseMixin, Orchestrator)`.
3. **Scope:** same-provider override only (`model_override` swaps `model`,
   not `provider`); `api/health.py`'s `/readyz` probe keeps pinging only the
   shared `ctx.llm`, not every override client; an empty/whitespace
   `model_override` is treated as unset via a truthy check, since
   pydantic-settings does not collapse `""` to `None` for a plain `str | None`
   field.

## Consequences

### Positive

- No edit, and no `BREAKING-CHANGE` trailer, needed on any protected path
  (`core/agent.py`, `core/orchestrator.py`).
- Reuses an existing, documented seam (`extras`) and an existing, proven
  pattern (`_HarnessOrchestrator`'s `_close_hooks()` override) instead of
  introducing new mechanisms.

### Negative / Trade-offs

- `_close_hooks()` is a single-underscore "private" method, not part of
  `Orchestrator`'s public surface (`aclose()` / `.context`). The mixin's
  correctness depends on that method remaining a stable override point across
  future `core/` refactors — a soft contract, not a hard one. This is an
  accepted, documented risk, not an oversight.
- `extras` is untyped (`dict[str, Any]`-shaped in practice); consumers of
  `agent_llm_overrides` must agree on the key and value shape by convention,
  with no protocol-level enforcement.

### Neutral

- Provider-level override (not just model) is out of scope; would need its
  own field and its own spec if ever needed.
- `/readyz` latency stays decoupled from how many overrides are configured.

## Alternatives Considered

- **New `AgentContext.llm_overrides` field:** rejected — forces a protected-path
  edit to `core/agent.py` for something `extras` already exists to handle.
- **Edit `core/orchestrator.py::_close_hooks()` directly:** more discoverable,
  but rejected because it requires a `BREAKING-CHANGE` trailer and
  protected-path review for a purely additive change that has a
  strictly-equivalent zero-protected-path alternative.

## References

- Code: `src/mangomas/config/agents.py:40` (`model_override` field),
  `src/mangomas/composition/builder.py::build_orchestrator`,
  `src/mangomas/composition/harness.py::_HarnessOrchestrator`,
  `src/mangomas/core/orchestrator.py::Orchestrator._close_hooks`
- Related ADRs: ADR-0019 (re-export facade / composition decomposition
  pattern this change's `composition/llm.py` mixin follows)
- Related specs: `specs/0028-per-agent-model-override.md`
