# ADR-0006: Configurable span exporter selection

## Status

Accepted

## Context

`configure_telemetry()` hard-wired a `ConsoleSpanExporter`, so a deployed
service had no way to ship traces to Cloud Trace or an OTLP collector without
editing source. The Claude Code harness additionally needed to route its
`harness.agent_invoke` spans to a *different* backend than application spans —
but in OpenTelemetry an exporter binds to a `TracerProvider`, not to a tracer
name. We needed a selection mechanism that stays default-OFF, avoids pulling
cloud SDKs into the base install, and can isolate harness telemetry.

## Decision

Add `TelemetrySettings` (`MANGOMAS_TELEMETRY__*`) with an `exporter` selector
(`console`/`otlp`/`gcp`) resolved through a generic
`exporter_registry: Registry[SpanExporterFactory]`. `otlp`/`gcp` factories
lazy-import their SDKs (optional extras `mangomas[otlp]` / `mangomas[gcp-trace]`)
and are registered on first selection. Harness routing uses
`HarnessSettings.metrics_exporter`; when set, `build_harness_tracer` builds a
dedicated `TracerProvider` that is **never** promoted via
`trace.set_tracer_provider`.

## Consequences

### Positive

- Cloud Trace / OTLP export is a config flag, not a code change.
- Optional SDKs stay out of the base install (lazy import + extras).
- Reuses the existing `Registry[T]` seam — adding an exporter is one `register`.
- Harness spans can target a separate backend without touching global state.

### Negative / Trade-offs

- A second, isolated `TracerProvider` exists when harness routing is enabled;
  operators must point each at the right backend.
- `BatchSpanProcessor` (used for network exporters) batches asynchronously, so
  spans are not flushed synchronously as with the console path.

### Neutral

- Default (`console` + `SimpleSpanProcessor`, `metrics_exporter=None`) is
  byte-identical to the prior behaviour; `configure_telemetry(telemetry=None)`
  still runs the legacy path.

## Alternatives Considered

- **Branch on exporter name inside `configure_telemetry`** — rejected; it would
  bake every cloud SDK import into one function and bypass the registry seam.
- **One global provider with a namespace-filtering processor for harness spans**
  — rejected; OTel processors filter by attributes awkwardly and it couples
  harness export to the app provider's lifecycle.

## References

- Code: `src/mangomas/telemetry_exporters.py`, `src/mangomas/telemetry.py`,
  `src/mangomas/config.py` (`TelemetrySettings`, `HarnessSettings`),
  `src/mangomas/composition.py` (`_HarnessOrchestrator.__init__`)
- Related ADRs: ADR-0001 (cloud targets)
- External: OpenTelemetry Python SDK trace export
