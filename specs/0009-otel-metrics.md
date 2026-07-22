# Spec-0009: OpenTelemetry metrics (MeterProvider)

- **Status:** Implemented
- **Linked ADR:** ADR-0013
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added`

## Problem

`telemetry.py` emits spans only — there is no `MeterProvider`, so there are no
counters or histograms to drive error-rate / latency SLO alerting (you cannot
cost-effectively compute p99 or error-rate off raw spans). This also leaves a
naming debt: the harness already advertises `MANGOMAS_HARNESS__METRICS_*` while
emitting only spans. This spec adds a metrics pipeline (a `MeterProvider` behind
the existing exporter-selection seam) and instruments the served agent-invocation
boundary, additive and default-OFF.

## Requirements

- A `MeterProvider` built alongside the `TracerProvider`, reusing the
  `console`/`gcp` exporter-selection tokens (`_build_span_exporter` → a parallel
  `_build_metric_reader`).
- Instruments emitted at the **HTTP `/agents/{name}/invoke` boundary** (not
  protected core): an invocation counter (`agent`, `status`), an error counter
  (`agent`, `code`), and a duration histogram (`agent`).
- Must remain **additive & default-OFF**: with `telemetry.metrics_enabled=false`
  (the default) no `MeterProvider` is installed, so the global provider stays the
  OTel no-op and the recording calls are free — behaviour is byte-identical.

## Config / env additions

| Env var (`MANGOMAS_*`) | Default | Purpose |
|------------------------|---------|---------|
| `MANGOMAS_TELEMETRY__METRICS_ENABLED` | `false` | Install a real `MeterProvider` |

Reuses `MANGOMAS_TELEMETRY__EXPORTER` (`console`/`gcp`) for the metric exporter.
New `DEFAULT_TELEMETRY_METRICS_ENABLED` constant.

## Protocol / contract impact

- New/changed protocols: _none_.
- New error types: _none_ — an unknown exporter reuses `ConfigError` via
  `_build_metric_reader` (mirrors `_build_span_exporter`).
- Registry additions: _none_.
- New module `src/mangomas/metrics.py` (record helpers) + `telemetry.py`
  additions (`configure_metrics`, `get_meter`, `_build_metric_reader`).

## Backwards-compatibility

- Default off → no `MeterProvider`; the global provider stays the no-op default,
  so instrument `.add()`/`.record()` calls are no-ops. Byte-identical.
- No protected-path edit (`core/orchestrator.py` untouched — emission is at the
  api boundary, per ADR-0013).

## Test plan

- Unit (`tests/test_metrics.py` + api tests): install a `MeterProvider` with an
  OTel `InMemoryMetricReader`, hit `/agents/{name}/invoke` (ok + unknown-agent),
  assert the invocation counter, error counter, and duration histogram record
  with the right attributes; assert the disabled default records nothing (no-op).
- Metric name/attribute constants in `tests/constants.py`.
- Coverage: maintain the 95% global gate and the api floor (95%).

## Acceptance criteria

- [x] Feature off by default → no `MeterProvider`; recording is a no-op.
- [x] Enabled → invoke increments the counter (`status=ok`), an unknown agent
      increments the error counter (`code=agent_not_found`), and the duration
      histogram records — proven with `InMemoryMetricReader`.
- [x] `ruff`, `mypy`, `pytest` (95% gate), `frontmatter-lint` all clean.
- [x] CHANGELOG updated; ADR-0013 added.
