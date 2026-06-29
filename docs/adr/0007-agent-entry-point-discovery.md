# ADR-0007: Agent entry-point discovery (dynamic agent loading)

## Status

Accepted

## Context

Agents were in-tree only: every agent had to be hand-registered in
`composition.py` via `agent_registry.register(...)`. Third-party (or separate
internal) packages could not ship an installable agent without editing this
repo. The evaluation harness already solved the equivalent problem for
scorers/sinks/targets/sources via `importlib.metadata` entry-point discovery
(`eval/discovery.py`, gated by `MANGOMAS_DISCOVERY_ENABLED`). We wanted the same
capability for agents without a new config surface, without changing the stable
`Agent` contract, and with zero behaviour change when disabled.

## Decision

Add `src/mangomas/agents/discovery.py` — a direct mirror of `eval/discovery.py`
for the agent registry:

- Entry-point group `mangomas.agents`, each pointing at an agent factory
  (`Callable[[AgentSettings | None], Agent]`, the same shape the built-in
  registrations use).
- `discover_agents(*, registry, group)` loads and registers each entry point;
  `ensure_agent_plugins(settings, registry)` runs it once per process when
  `settings.discovery_enabled` is true.
- Wiring lives in `build_orchestrator`, which calls `ensure_agent_plugins` once
  immediately before iterating `agent_registry.available()`. This is the shared
  chokepoint for **both** the CLI and the API lifespan (an improvement over
  eval's CLI-only wiring).
- The **existing** `MANGOMAS_DISCOVERY_ENABLED` flag is reused (its own config
  comment already reads "enable entry-point-based agent discovery").

`agents/discovery.py` takes the registry as a parameter rather than importing
`composition.agent_registry`, so it never imports `composition` (which imports
the concrete agents) — avoiding an import cycle.

## Consequences

### Positive

- `pip install some-agent` + `MANGOMAS_DISCOVERY_ENABLED=true` makes a
  third-party agent dispatchable with zero repo changes.
- Reuses the proven `Registry[T]` seam and the eval discovery semantics
  (fault-isolated load, last-call-wins override, once-per-process latch).
- One config flag covers both eval and agent discovery.

### Negative / Trade-offs

- A plugin can override a built-in agent name (last-call-wins). This is
  intentional but logged at INFO so it is auditable.
- Discovery cost (entry-point scan) is paid once per process when enabled.

### Neutral

- Default (`discovery_enabled=False`) is byte-identical to today:
  `ensure_agent_plugins` returns immediately and only the in-tree agents wire.

## Alternatives Considered

- **A separate `MANGOMAS_AGENT_DISCOVERY_ENABLED` flag** — rejected; one
  "enable entry-point discovery" switch is simpler and the existing flag was
  already documented as covering agents.
- **Wire discovery in the CLI only (as eval does)** — rejected; agents are wired
  in `build_orchestrator`, which both the CLI and the API call, so that is the
  correct single chokepoint.
- **Validate the constructed `Agent` at discovery time** — rejected; factories
  need `AgentSettings` to construct, so construction stays in the existing
  build loop. Discovery validates the entry point is callable and isolates load
  failures.

## References

- Code: `src/mangomas/agents/discovery.py`,
  `src/mangomas/composition.py` (`build_orchestrator`),
  `src/mangomas/agents/__init__.py`
- Mirrors: `src/mangomas/eval/discovery.py`
- Related ADRs: ADR-0004 (eval target/source indirection — same discovery pattern)
- External: Python `importlib.metadata` entry points
