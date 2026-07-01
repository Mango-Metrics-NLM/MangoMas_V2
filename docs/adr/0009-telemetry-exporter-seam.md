# ADR-0009: Telemetry exporter selection seam

## Status

Accepted

## Context

`NEXT_STEPS.md` asks for (a) a Cloud Trace exporter selectable behind the
existing `configure_telemetry()` and (b) routing the harness
`harness.agent_invoke` spans to a *different* exporter than application spans.
Today `configure_telemetry()` hard-wires a single `ConsoleSpanExporter` on the
global `TracerProvider`, and the harness tracer is only a differently-*named*
tracer on that same global provider — so a namespace alone cannot reroute its
spans.

## Decision

Add a shared `_build_span_exporter(token)` helper (`console` → built-in,
`gcp` → lazily-imported Cloud Trace exporter behind the `gcp` extra).
`configure_telemetry(..., exporter=...)` uses it for application spans, selected
by `MANGOMAS_TELEMETRY__EXPORTER`. A new `build_scoped_tracer(namespace, *,
exporter)` returns the global tracer when `exporter="inherit"` (default, no
change) or, for `console`/`gcp`, builds a **dedicated `TracerProvider`** so the
harness tracer's spans are routed independently. Selected by
`MANGOMAS_HARNESS__METRICS_EXPORTER` (default `inherit`).

## Consequences

### Positive

- Cloud Trace export is opt-in via one env var; no new call sites.
- Harness spans can be shipped to a separate exporter without disturbing
  application tracing.
- Exporter selection lives in one helper, reused by both paths.

### Negative / Trade-offs

- A non-`inherit` harness exporter creates a second `TracerProvider` (extra
  span processor). Acceptable — engaged only when explicitly configured.
- Cloud Trace has no arbitrary-endpoint knob, so no `endpoint` field is exposed
  (would be dead config against the current exporter set).

### Neutral

- Both env vars absent → a single console exporter, byte-identical to today.
- Cloud SDK stays optional + lazy; module imports without the `gcp` extra.

## Alternatives Considered

- **Filtering span processor on the global provider** — rejected: more complex
  and couples app/harness export into one processor.
- **Expose an OTLP endpoint field** — rejected: no OTLP exporter dependency
  ships today, so the field would be unusable.

## References

- Code: `src/mangomas/telemetry.py` (`_build_span_exporter`,
  `configure_telemetry`, `build_scoped_tracer`), `src/mangomas/composition.py`
  (`_HarnessOrchestrator`), `src/mangomas/config.py` (`TelemetrySettings`,
  `HarnessSettings.metrics_exporter`)
- Specs: `specs/0001-telemetry-exporter.md`, `specs/0002-harness-metrics-exporter.md`
- Related ADRs: ADR-0001 (cloud targets)
