# Observability

Mango-Mas V2 emits structured logs + OpenTelemetry spans on every request,
all tied together by a per-request **correlation id**. This document
explains the moving parts.

## Metrics (opt-in; ADR-0013)

Alongside spans, an opt-in `MeterProvider` (default-OFF — enable with
`MANGOMAS_TELEMETRY__METRICS_ENABLED=true`) emits three instruments at the
`POST /agents/{name}/invoke` boundary: `mangomas.agent.invocations` (counter,
attributes `agent` + `status`), `mangomas.agent.errors` (counter, `agent` +
`code`), and `mangomas.agent.duration` (histogram, `agent`). The metric
exporter reuses the same `console` / `gcp` selection seam as the span exporter
(`telemetry._build_metric_reader` mirrors `_build_span_exporter`); when the
feature is off the global provider stays the OTel no-op, so recording is free.
The record helpers live in `src/mangomas/metrics.py`.

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

- **`src/mangomas/correlation.py`** — `ContextVar`, `CorrelationFilter`,
  `set_correlation_id()`, `get_correlation_id()`, `generate_correlation_id()`.
- **`src/mangomas/api/middleware.py`** — `AccessLogMiddleware` reads/echoes
  `X-Request-ID`, sets the ContextVar, attaches OTel baggage, and detaches
  cleanly in the `finally` block so the ContextVar does not leak across
  requests.
- **`mangomas.telemetry`** — `configure_telemetry()` attaches both
  `TraceContextFilter` and `CorrelationFilter` to the configured handler,
  so every log record across the codebase carries all three identifiers.
- **`src/mangomas/api/tracing.py`** — separate `TraceMiddleware` opens a
  per-request span and propagates `traceparent` / `tracestate` headers.

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

## What ships when

- **v0.2.0:** ContextVar, filter, middleware integration, OTel baggage push,
  W3C trace propagation via `TraceContextTextMapPropagator`.
- **Shipped (Milestone C):** the Cloud Trace exporter swap — setting
  `MANGOMAS_TELEMETRY__EXPORTER=gcp` replaces `ConsoleSpanExporter` with a
  lazily imported `CloudTraceSpanExporter`
  (`telemetry/exporters.py::_lazy_cloud_trace_exporter`, `gcp` extra
  required). Structured logs reach Cloud Logging via the JSON log format
  (`MANGOMAS_LOG__FORMAT=json`): Cloud Run ingests JSON stdout lines
  natively, so no dedicated log-sink adapter is needed.
- **Still open:** per-tenant correlation id partitioning.
