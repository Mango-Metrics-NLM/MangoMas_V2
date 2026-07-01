# ADR-0008: Dynamic agent loading via entry points

## Status

Accepted

## Context

`NEXT_STEPS.md` ("Agent marketplace / dynamic loading") calls for third-party
packages to register agents into `agent_registry` without editing
`composition.py`. The eval harness already solved the equivalent problem for
scorers/sinks/targets/sources in `eval/discovery.py`, gated by
`MANGOMAS_DISCOVERY_ENABLED`. We must decide how agent discovery handles a
plugin whose name collides with a **built-in** agent (`chat`, `summarize`,
`tool`, `planner`, `reviewer`).

## Decision

Add `agents/discovery.py` mirroring `eval/discovery.py` (same `entry_points`
call, once-per-process latch, log-and-skip on plugin failure), reusing the
existing `discovery_enabled` flag and the `mangomas.agents` entry-point group.
A discovered agent whose name collides with a built-in is **skipped with a
WARNING** — the opposite of eval's last-call-wins override. The "protected" set
is the registry's contents captured *before* discovery runs, so no built-in
names are hard-coded.

## Consequences

### Positive

- Third-party agents install and register with zero edits to `composition.py`.
- Built-in agents can never be silently replaced by an installed package —
  the core dispatch surface stays predictable and safe.
- Reuses the proven eval-discovery machinery and its test technique.

### Negative / Trade-offs

- Behaviour diverges from eval discovery (override vs. skip-on-builtin), which
  callers must know. Justified: replacing `chat` is far higher-risk than
  overriding a scorer.
- Collisions between two third-party agents keep last-call-wins (unprotected);
  documented, not surprising.

### Neutral

- Default-OFF (`discovery_enabled=False`) → registry contents byte-identical to
  today.

## Alternatives Considered

- **Last-call-wins for agents too** — rejected: lets an installed package
  hijack a built-in agent name silently.
- **Hard error on any collision** — rejected: one bad plugin would break
  orchestrator construction for everyone; skip-and-warn is more resilient.

## References

- Code: `src/mangomas/agents/discovery.py`, `src/mangomas/composition.py`
  (`build_orchestrator` → `ensure_agent_plugins`), `src/mangomas/eval/discovery.py`
- Spec: `specs/0006-dynamic-agent-loading.md`
- Related ADRs: ADR-0004 (eval target/source indirection)
