# Spec-0026: Orchestrator-level metrics + LoopSettings wiring (governed Batch B-b)

- **Status:** Implemented
- **Linked ADR:** [ADR-0026](../docs/adr/0026-orchestrator-metrics-and-loop-wiring.md)
  (metric-seam relocation into `dispatch`/`_stream_agent`; `LoopSettings`
  activation; `StepTimeout` 504)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Changed` (when implemented)
- **Origin:** `docs/analysis/20260822-next-steps-roadmap-analysis.md` §3
  Phase 1, item 1.2(b) — ADR-0013's recorded deferral ("emission inside
  `orchestrator.dispatch` … deferred until a metrics need spans CLI +
  programmatic dispatch, at which point an ADR blesses it")

## Problem

Two shipped claims are false today. First, ADR-0013's metrics count only the
HTTP `/agents/{name}/invoke` + `/stream` boundary, so CLI dispatch, workflow
nodes, and pipeline/fan-out inner steps are invisible to the
invocation/error/duration instruments — the exact gap ADR-0013 deferred and
called for a companion ADR to close. Second, both `LoopSettings` fields
(`MANGOMAS_LOOP__MAX_STEPS`, `MANGOMAS_LOOP__STEP_TIMEOUT_SECONDS`) are dead
config: documented, defaulted, validated by the env-example contract — and
read by nothing. No per-step timeout exists anywhere in the dispatch path.

This is protected-path work: the governance contract is **path-based, not
signature-based** — any change to `core/orchestrator.py` (and here also
`errors.py`) requires a `BREAKING-CHANGE` trailer even where behaviourally
additive.

## Requirements

### R1 — Metric emission relocates into the orchestrator

- `Orchestrator.dispatch` records `record_agent_invocation(name, "ok"/"error")`,
  `record_agent_error(name, exc.code)` for any `MangomasError` (including the
  pre-loop `AgentNotFound`), and `record_agent_duration(name, elapsed)` around
  the **whole call** (loop + persistence); duration is recorded on the ok path
  only, preserving the route's prior semantics.
- **Per-invocation semantics**: `dispatch_pipeline` and `dispatch_fan_out`
  delegate through `dispatch`, so **each inner agent invocation counts as one
  invocation point** — a 3-agent pipeline emits 3 ok points, not 1. Recorded
  deliberately in ADR-0026.
- `_stream_agent` takes over the stream metrics with spec-0025's symmetric rule
  intact: **full drain ⇔ persisted ⇔ counted** (ok + duration on full drain,
  after successful persistence; error metric on a `MangomasError` mid-drain or
  pre-stream — `stream_dispatch`'s eager `AgentNotFound` check records it;
  consumer abandonment records nothing).
- The now-duplicate emission is **removed** from `api/routes/agents.py` (both
  the invoke and stream handlers) so nothing double-counts; the route keeps
  only HTTP concerns (SSE framing, the `metadata`/`done` events, the
  truncation contract). Instrument names and labels are unchanged.
- **Visible change** (recorded in ADR-0026): the instruments now include
  non-HTTP dispatch traffic (CLI, workflow nodes, inner pipeline/fan-out
  steps). Dashboards keyed on the instruments see higher, more truthful counts.
- **Layering note**: `core/orchestrator.py` gains a runtime import of
  `mangomas.metrics` — a telemetry leaf over the OTel API the module already
  imports (`opentelemetry.trace`), not a domain dependency. Recorded in
  ADR-0026 and in `core/CLAUDE.md`'s import invariant.

### R2 — LoopSettings wiring

- `Orchestrator.__init__` gains a **keyword-only, optional**
  `loop_settings: LoopSettings | None = None` (TYPE_CHECKING import). The
  default `None` preserves current behaviour exactly — additive signature
  change; no positional/keyword semantics change for any existing caller.
- `composition.py::build_orchestrator` passes `cfg.loop` to both the bare
  `Orchestrator` and `_HarnessOrchestrator` constructions.
- **Per-step timeout**: when `loop_settings` is provided, each per-step
  `agent.handle(...)` in `dispatch`'s loop is wrapped in
  `asyncio.timeout(loop_settings.step_timeout_seconds)`; a `TimeoutError`
  raises the new `StepTimeout`. The field defaults to `30.0` (non-None), so
  **composition-wired deployments now enforce the documented default cap —
  that is the whole point: the env var claimed this behaviour all along.**
  A bare `Orchestrator(ctx)` (tests, embedders) keeps today's
  no-timeout behaviour. Streaming is **not** timeout-wrapped in this batch
  (out of scope; a stream's natural budget is the consumer's, and a per-step
  clock has no single step to bound — follow-up if needed).
- **`max_steps` precedence** (highest wins):
  1. the explicit `max_steps` kwarg;
  2. `request.max_steps`, when it differs from the `AgentRequest` field
     default (`1`);
  3. `loop_settings.max_steps`;
  4. the `AgentRequest` field default.
  Rationale: `MANGOMAS_LOOP__MAX_STEPS` was always documented as the loop cap
  and was silently ignored; a caller who explicitly set `request.max_steps`
  or the kwarg has stated intent that beats the deployment default. The
  defaults coincide at `1`, so behaviour is unchanged unless the operator
  sets the env var. The request-field tier is default-sentinel-based: a caller
  who explicitly sets `request.max_steps = 1` is indistinguishable from the
  default and falls through to the settings tier — acceptable because both
  spell "single shot" unless the operator raised the deployment cap, in which
  case the kwarg is the precise override.

### R3 — `StepTimeout` typed error (three-file lock-step)

- `errors.py`: `StepTimeout(MangomasError)`, `code="step_timeout"`, with a
  `.seconds` attribute.
- `api/errors.py::_ERROR_STATUS`: `StepTimeout → 504` (Gateway Timeout — the
  server-side budget elapsed while waiting on the agent's upstream work,
  mirroring `LLMTimeout`'s 504).
- `tests/test_errors.py`: status walk + exhaustive-subclass walk updated.

## Scenarios (WHEN/THEN)

- WHEN an agent step exceeds `step_timeout_seconds` (loop_settings provided)
  THEN `StepTimeout` is raised, the error metric records `step_timeout`, and
  no turn is persisted. Guard can fire: removing the `asyncio.timeout` wrap
  fails the test (`mango-mutation-proof`).
- WHEN `loop_settings` is `None` THEN a slow agent completes exactly as today
  (regression pin — the gate passes non-vacuously).
- WHEN `orch.dispatch` is called directly (CLI-style, no HTTP) THEN ok/error/
  duration points appear. Guard can fire: removing the orchestrator emission
  fails the HTTP end-to-end metric test too (the route no longer emits).
- WHEN a request flows through `POST /agents/{name}/invoke` THEN the
  invocation counter increments **exactly once**. Guard can fire: re-adding
  route-level emission fails the no-double-count test.
- WHEN a pipeline or fan-out runs THEN each inner agent invocation emits its
  own point.
- WHEN `loop_settings.max_steps` is set and neither the kwarg nor a
  non-default `request.max_steps` is present THEN the settings value caps the
  loop; each higher-precedence tier overrides the tiers below it.
- WHEN `loop_settings` carries the defaults (`max_steps=1`) THEN behaviour is
  byte-identical to today's single-shot dispatch (default-coincidence pin).

## Config / env additions

_None new._ Two existing (previously dead) vars become live:

| Env var (`MANGOMAS_*`) | Default | Purpose |
|------------------------|---------|---------|
| `MANGOMAS_LOOP__MAX_STEPS` | `1` | Orchestrator loop cap — now honoured (precedence: kwarg > request > this) |
| `MANGOMAS_LOOP__STEP_TIMEOUT_SECONDS` | `30.0` | Per-step timeout — now enforced on composition-wired deployments |

Defaults are `DEFAULT_LOOP_MAX_STEPS` / `DEFAULT_LOOP_STEP_TIMEOUT` module
constants — unchanged.

## Protocol / contract impact

- New/changed protocols: _none_. `Orchestrator.__init__` gains a keyword-only
  optional parameter; `dispatch`'s positional argument names are unchanged.
- New error types: `StepTimeout` (`code="step_timeout"`, 504) via the
  three-file lock-step.
- Registry additions: _none_.
- **Governance**: the commit series carries a `BREAKING-CHANGE` trailer
  (path-based obligation: `core/orchestrator.py` + `errors.py`), lands as its
  own PR per the small-governed-batch rule, with companion ADR-0026 closing
  ADR-0013's recorded deferral.

## Backwards-compatibility

- `dispatch(name, request)` with no other args behaves identically
  (`loop_settings=None` for direct construction; composition-wired defaults
  coincide: `max_steps=1`, and the 30 s step cap only bites an agent step that
  already exceeded the documented budget).
- Existing OTel span names (`orchestrator.dispatch`, etc.) are unchanged; the
  dispatch span gains an additive `loop.step_timeout_seconds` attribute when a
  timeout is configured.
- Instrument names/labels unchanged; metric **counts** now include non-HTTP
  traffic (the deliberate, recorded change).
- Metrics stay opt-in (`MANGOMAS_TELEMETRY__METRICS_ENABLED`, default off) —
  the record helpers are no-ops against the no-op provider.

## Test plan

- Unit (`tests/test_control_loop.py`): timeout fires (`StepTimeout`, no turn
  persisted); `loop_settings=None` regression pin; all four precedence tiers;
  default-coincidence pin; fast agent under the default cap.
- Metrics (`tests/test_metrics.py`): direct-dispatch ok/error/duration;
  `step_timeout` error code point; pipeline/fan-out inner-step counting;
  HTTP end-to-end tests retained (now proving the orchestrator-seam emission
  through a route call); exactly-once counting through the route (both
  invoke and stream).
- Errors (`tests/test_errors.py`): `StepTimeout` in the hierarchy/code/status
  walks; `.seconds` attribute.
- Mutation proofs (run + reported, not committed): (1) remove orchestrator
  dispatch emission → HTTP end-to-end metric test fails; (2) re-add
  route-level emission → no-double-count test fails; (3) remove the
  `asyncio.timeout` wrap → timeout test fails.
- Coverage: `core/**` and `errors` stay at their 100 % floors.

## Acceptance criteria

- [x] Direct (CLI-style) dispatch emits invocation/error/duration points,
  proven by test; HTTP path still emits end-to-end, exactly once.
- [x] `StepTimeout` lock-step complete (errors.py + 504 mapping + walks).
- [x] `LoopSettings` fields are live: timeout + max_steps tests green,
  including the two regression pins.
- [x] No public signature broke (`dispatch` positional names unchanged;
  `loop_settings` keyword-only optional).
- [x] `BREAKING-CHANGE` trailer present (coordinator's commit); ADR-0026
  authored and Accepted; ADR-0013 deferral note closed.
- [x] `ruff`, `mypy`, `pytest` (95 % gate + per-package floors) all clean.
- [ ] CHANGELOG updated (coordinator's).
