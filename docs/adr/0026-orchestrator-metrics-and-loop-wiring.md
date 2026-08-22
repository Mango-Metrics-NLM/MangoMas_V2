# ADR-0026: Orchestrator-level metric emission + loop-settings wiring

## Status

Accepted

## Context

ADR-0013 placed the agent instruments at the HTTP `/agents/{name}/invoke`
boundary and recorded Option A — emission inside `core/orchestrator.py::dispatch`
— as deferred "until a metrics need spans CLI + programmatic dispatch, at which
point an ADR blesses it". That need is now confirmed: CLI dispatch, workflow
nodes, and pipeline/fan-out inner steps are invisible to the instruments. In
the same protected file, both `LoopSettings` fields (`MANGOMAS_LOOP__*`) are
documented, contract-tested config that nothing reads — no per-step timeout
exists anywhere.

## Decision

Metric emission moves into the orchestrator — `dispatch` records
invocation/error/duration around the whole call, `_stream_agent` keeps
spec-0025's full-drain ⇔ persisted ⇔ counted rule — and the duplicate route
emission is removed. `Orchestrator.__init__` gains keyword-only
`loop_settings: LoopSettings | None = None`; when wired (composition passes
`cfg.loop`), each step runs under `asyncio.timeout(step_timeout_seconds)`
(→ new `StepTimeout`, 504) and `max_steps` resolves kwarg > non-default
`request.max_steps` > `loop_settings.max_steps` > field default.

## Consequences

### Positive

- One truthful chokepoint: every invocation — HTTP, CLI, workflow node,
  pipeline/fan-out inner step — is counted; **each inner step is its own
  point** (a 3-agent pipeline emits 3, deliberately).
- The `MANGOMAS_LOOP__*` env vars finally do what they have always claimed;
  defaults coincide (`max_steps=1`), so nothing changes until an operator sets
  them — except the 30 s step cap now genuinely binds composition-wired
  deployments, which is the point.
- No double-count: the route keeps only HTTP concerns.

### Negative / Trade-offs

- Metric counts visibly rise on existing dashboards: non-HTTP traffic is now
  included, and pipelines/fan-outs count per inner step.
- `core/orchestrator.py` gains a runtime import of `mangomas.metrics` — a
  telemetry leaf over the OTel API the module already imports, not a domain
  dependency; the `core/` import invariant is amended to name it.
- A directly constructed `Orchestrator(ctx)` still has no timeout — the cap is
  a wiring concern, engaged by the composition root.

### Neutral

- Instrument names/labels, span names, and `dispatch`'s positional argument
  names are unchanged; the dispatch span gains `loop.step_timeout_seconds`.
- Streaming is not timeout-wrapped in this batch (recorded out of scope in
  spec-0026); abandonment still records nothing.

## Alternatives Considered

- **Keep boundary emission and add CLI/workflow emission points** — rejected:
  scatter invites double-counting; ADR-0013 already named the orchestrator the
  single truthful chokepoint.
- **A separate per-step timer task** — rejected: `asyncio.timeout` is the
  stdlib structured-concurrency primitive; no hand-rolled timer.
- **Settings beat `request.max_steps`** — rejected: an explicit caller value
  is stated intent; the deployment default fills silence only.

## References

- Code: `src/mangomas/core/orchestrator.py` (`dispatch`, `_stream_agent`),
  `src/mangomas/api/routes/agents.py`, `src/mangomas/composition.py`,
  `src/mangomas/errors.py` (`StepTimeout`), `src/mangomas/config/agents.py`
  (`LoopSettings`).
- Spec: `specs/0026-orchestrator-metrics-loop-wiring.md`
- Related ADRs: ADR-0013 (the deferral this closes), ADR-0025 (streaming
  full-drain rule), ADR-0021 (governance contract)
