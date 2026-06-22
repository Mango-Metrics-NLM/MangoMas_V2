# Observability

Mango-Mas V2 emits structured logs + OpenTelemetry spans on every request,
all tied together by a per-request **correlation id**. This document
explains the moving parts.

## Three identifiers, one request

| Identifier | Source | Lifetime | Where it appears |
|---|---|---|---|
| `trace_id` (W3C) | OpenTelemetry tracer (`TraceMiddleware`) | One request, propagated cross-service via `traceparent` | Span attributes; log records (via `TraceContextFilter`) |
| `span_id` | OpenTelemetry tracer | One operation within the trace | Span attributes; log records |
| `correlation_id` | `AccessLogMiddleware` (reads `X-Request-ID` or generates) | One request | Response header `X-Request-ID`; OTel baggage key `mangomas.correlation_id`; log records (via `CorrelationFilter`); ContextVar `mangomas.api.correlation.correlation_id` |

The W3C ids are great for distributed-tracing tooling. The correlation id
is the **user-facing** handle: it's what appears in support tickets, gets
echoed back on the HTTP response, and travels with downstream HTTP calls
via OTel baggage.

## Request lifecycle

```text
                                      ┌──────────────────────────────────┐
client ──► X-Request-ID: abc12345 ──► │ AccessLogMiddleware              │
                  (or absent)         │   1. resolve correlation_id      │
                                      │      (inbound header or fresh)   │
                                      │   2. correlation_id.set(...)     │
                                      │   3. baggage.set_baggage(...)    │
                                      │   4. context.attach(...)         │
                                      └──────────────┬───────────────────┘
                                                     │
                                      ┌──────────────▼───────────────────┐
                                      │ TraceMiddleware                  │
                                      │   start span; propagate trace    │
                                      └──────────────┬───────────────────┘
                                                     │
                                      ┌──────────────▼───────────────────┐
                                      │ Route handler / Orchestrator     │
                                      │   logger.info(...) - records     │
                                      │   carry trace_id, span_id,       │
                                      │   correlation_id automatically   │
                                      └──────────────┬───────────────────┘
                                                     │
                              ◄──────── X-Request-ID: abc12345 echoed ────┘
                              ◄──────── correlation_id detached from
                                        ContextVar and OTel context
```

## Code map

- **`src/mangomas/api/correlation.py`** — `ContextVar`, `CorrelationFilter`,
  `set_correlation_id()`, `get_correlation_id()`, `generate_correlation_id()`.
- **`src/mangomas/api/middleware.py`** — `AccessLogMiddleware` reads/echoes
  `X-Request-ID`, sets the ContextVar, attaches OTel baggage, and detaches
  cleanly in the `finally` block so the ContextVar does not leak across
  requests.
- **`src/mangomas/telemetry.py`** — `configure_telemetry()` attaches both
  `TraceContextFilter` and `CorrelationFilter` to the configured handler,
  so every log record across the codebase carries all three identifiers.
- **`src/mangomas/api/tracing.py`** — separate `TraceMiddleware` opens a
  per-request span and propagates `traceparent` / `tracestate` headers.
- **`src/mangomas/telemetry_exporters.py`** — `exporter_registry`
  (a generic `Registry[SpanExporterFactory]`), `resolve_exporter()`, and
  `build_harness_tracer()`. Selects the span exporter and span processor.

## Span exporters

The span exporter is selected at startup via `TelemetrySettings`
(`MANGOMAS_TELEMETRY__*`) and resolved through `exporter_registry` —
the same `Registry[T]` pattern used for LLM/storage/secrets providers.
Optional exporter SDKs are imported lazily, so the default install never
pulls a cloud dependency.

| `MANGOMAS_TELEMETRY__EXPORTER` | Backend | Processor | Extra |
|---|---|---|---|
| `console` *(default)* | `ConsoleSpanExporter` (stdout) | `SimpleSpanProcessor` | — |
| `otlp` | OTLP/gRPC → `MANGOMAS_TELEMETRY__OTLP_ENDPOINT` | `BatchSpanProcessor` | `mangomas[otlp]` |
| `gcp` | Cloud Trace → `MANGOMAS_TELEMETRY__GCP_PROJECT_ID` (ADC) | `BatchSpanProcessor` | `mangomas[gcp-trace]` |

`console` keeps the synchronous `SimpleSpanProcessor` (deterministic for
local runs and tests); network backends use `BatchSpanProcessor`. The
default (`console`) is byte-identical to the historical behaviour — when
`configure_telemetry` is called without a `TelemetrySettings`, the legacy
console path runs unchanged.

### Harness span routing

`HarnessSettings.metrics_exporter` (`MANGOMAS_HARNESS__METRICS_EXPORTER`)
optionally routes `harness.agent_invoke` spans to a **separate** backend
than application spans. When unset (`None`, the default) the harness shares
the global application tracer. When set, `build_harness_tracer` constructs a
*dedicated, isolated* `TracerProvider` that is **never** promoted globally —
so harness telemetry can target a different endpoint without mutating global
trace state. See [ADR-0006](../adr/0006-span-exporter-selection.md).

## JSON log envelope

When `MANGOMAS_LOG__FORMAT=json`, the `JsonFormatter` emits records like:

```json
{
  "timestamp": "2026-05-16T12:00:00.123Z",
  "severity": "INFO",
  "logger": "mangomas.api.middleware",
  "message": "POST /agents/chat/invoke 200 42.1ms",
  "request_id": "abc12345",
  "correlation_id": "abc12345",
  "trace_id": "0af7651916cd43dd8448eb211c80319c",
  "span_id": "b7ad6b7169203331",
  "method": "POST",
  "path": "/agents/chat/invoke",
  "status_code": 200,
  "latency_ms": 42.1
}
```

`request_id` and `correlation_id` always carry the same value today;
`request_id` is preserved for backwards-compatible log consumers.

## Configuring the correlation header

The middleware accepts any non-blank `X-Request-ID` value verbatim:
clients and upstream proxies can supply their own ids. When absent or
blank, a fresh 8-hex-character token is generated.

To disable the inbound-header pathway entirely (e.g. to force regeneration
for security reasons), strip the header at the edge — there is no
runtime flag for this today.

## What ships in v0.2.0 vs later

- **v0.2.0:** ContextVar, filter, middleware integration, OTel baggage push,
  W3C trace propagation via `TraceContextTextMapPropagator`.
- **Shipped since:** configurable span exporter (`console`/`otlp`/`gcp`) and
  optional dedicated harness span routing — see "Span exporters" above.
- **Deferred:** Cloud Logging structured-log sink, per-tenant correlation id
  partitioning.
