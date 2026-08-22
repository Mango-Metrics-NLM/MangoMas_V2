# ADR-0013: OpenTelemetry metrics meter seam

## Status

Accepted

## Context

`telemetry.py` ships spans only — no `MeterProvider`, no counters/histograms — so
error-rate and latency SLOs have no metric substrate, and the harness's
`METRICS_*` settings advertise capability that does not exist. We need a metrics
pipeline that reuses the shipped exporter-selection seam, stays default-OFF, and
does not edit protected core. The open question is *where* to emit the
invocation counter: the orchestrator's `dispatch` is the single truthful
chokepoint but `core/orchestrator.py` is a protected path.

## Decision

Add a `MeterProvider` in `telemetry.py` behind a `_build_metric_reader` parallel
to `_build_span_exporter`, gated by a default-OFF `TelemetrySettings.metrics_enabled`.
Emit instruments at the **HTTP `/agents/{name}/invoke` boundary** (Option B), not
inside `core/orchestrator.py` (Option A) — keeping the "avoid protected core"
invariant. When metrics are disabled the global provider stays the OTel no-op, so
the recording calls compile away to nothing.

## Consequences

### Positive

- Metric substrate for alerting/SLOs; pays down the harness `METRICS_*` naming
  debt; zero protected-path edits; unknown-exporter reuses `ConfigError`.
- Recording helpers are no-ops against the default no-op provider, so the hot
  path is unaffected when the feature is off.

### Negative / Trade-offs

- v1 counts the **HTTP served surface only**: streaming, the workflow route,
  CLI dispatch, and adapter-level LLM latency are not yet instrumented. These are
  documented follow-ups; the api boundary is chosen because it is the
  Cloud-Run-served surface where SLOs matter first. *(Deferral closed by
  ADR-0026: emission moved into the orchestrator; streaming was instrumented
  by spec-0025/ADR-0025.)*

### Neutral

- Metric-exporter selection reuses the `console`/`gcp` token from
  `TelemetrySettings.exporter`; a periodic reader flushes to the exporter.
- Instruments live in a new `src/mangomas/metrics.py` (record helpers); tests
  bind them to an `InMemoryMetricReader`.

## Alternatives Considered

- **Option A — emit inside `core/orchestrator.py::dispatch`** — rejected for v1:
  it is the single truthful chokepoint but a protected-path edit; deferred until a
  metrics need spans CLI + programmatic dispatch, at which point an ADR blesses it.
  *(That ADR is ADR-0026, which adopts Option A and closes this deferral.)*
- **Boundary emission scattered across api + workflow + cli** — deferred: start
  with the one served surface (`/agents/{name}/invoke`) to avoid double-counting
  and premature scatter.

## References

- Code: `src/mangomas/telemetry/meters.py` (`configure_metrics`, `get_meter`),
  `telemetry/exporters.py` (`_build_metric_reader`), `src/mangomas/metrics.py`,
  `src/mangomas/api/routes/agents.py` (`/agents/{name}/invoke`),
  `src/mangomas/config/observability.py::TelemetrySettings`.
- Related: ADR-0009 (telemetry-exporter seam — the pattern mirrored here);
  spec `specs/0009-otel-metrics.md`.
