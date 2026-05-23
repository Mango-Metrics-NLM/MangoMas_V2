---
name: mango-observability
description: >
  Telemetry, structured logging, and tracing in Mango-Mas V2. Use when:
  adding a new span around a code path, attaching structured fields to a
  log line, propagating correlation IDs, switching log format
  (text vs json), or diagnosing a missing/duplicate span in the OTel
  console exporter. Covers the existing telemetry.py bootstrap,
  TraceContextFilter, CorrelationFilter, and orchestrator span conventions.
argument-hint: "Describe the code path to instrument or paste a log line that needs more context"
---

# Mango-Mas Observability Skill

## When to Use

- Add a new OpenTelemetry span around an adapter, agent, or pipeline step
- Attach structured fields to a log line (`extra={...}`)
- Propagate a correlation ID through async work
- Switch log output between `text` and `json` formats
- Diagnose a missing or duplicate span in the OTel console exporter
- Wire a new harness-level tracer (e.g. `mangomas.harness`)

---

## Quick Commands

```powershell
# Run the telemetry test
python -m pytest tests/test_telemetry.py -v

# Run with JSON logs (matches the production format)
$env:MANGOMAS_LOG__FORMAT='json' ; python -m mangomas.cli.main chat "hello" ; Remove-Item Env:\MANGOMAS_LOG__FORMAT

# Run an integration scenario and inspect the console-exported spans
$env:RUN_LMSTUDIO='1' ; python -m pytest tests/lmstudio/test_chat_invoke.py -v -s
```

```bash
python -m pytest tests/test_telemetry.py -v
MANGOMAS_LOG__FORMAT=json python -m mangomas.cli.main chat "hello"
RUN_LMSTUDIO=1 python -m pytest tests/lmstudio/test_chat_invoke.py -v -s
```

---

## Observability Rules

| Rule | Detail |
|------|--------|
| One tracer per module | `_tracer = get_tracer(__name__)` at module top; reuse for every span. |
| Span naming | `<layer>.<component>.<action>` (e.g. `agent.chat.execute`, `adapter.lmstudio.complete`, `orchestrator.dispatch`). |
| Set attributes, not log lines | Span attributes: `agent.name`, `messages.count`, `tool.name`, `error.code`. Use `span.set_attribute(...)`. |
| Structured logs | Always `logger.info(msg, extra={"key": value})`. Never f-string the context into the message. |
| Correlation propagation | Use `set_correlation_id(...)` from `mangomas.correlation` — `CorrelationFilter` picks it up automatically. |
| No new exporter | Reuse the singleton bootstrap in `telemetry.py::configure_telemetry`. Do not create a parallel `TracerProvider`. |
| Idempotent setup | `configure_telemetry()` is safe to call multiple times — first call wins, locked via `_TelemetryState`. |

---

## Reference

| File | Role |
|------|------|
| `src/mangomas/telemetry.py` | `get_tracer`, `configure_telemetry`, `JsonFormatter`, `TraceContextFilter` |
| `src/mangomas/correlation.py` | Correlation `ContextVar` + `CorrelationFilter` (picked up by both formatters) |
| `src/mangomas/core/orchestrator.py` | Reference span usage in `dispatch`, `dispatch_pipeline`, `dispatch_fan_out`, `stream_dispatch` |
| `src/mangomas/api/middleware.py` | `AccessLogMiddleware` — HTTP-layer instrumentation example |
| `src/mangomas/api/tracing.py` | HTTP request span emitter |
| `docs/architecture/observability.md` | Architectural overview |
| `tests/test_telemetry.py` | Coverage of the bootstrap + JSON output |

---

## Existing Span Inventory (orchestrator)

| Span name | Attributes |
|-----------|------------|
| `orchestrator.dispatch` | `agent.name`, `messages.count`, `loop.max_steps`, `loop.steps_taken`, `loop.accepted` |
| `orchestrator.dispatch_pipeline` | `topology="pipeline"`, `agent_count` |
| `orchestrator.dispatch_fan_out` | `topology="fan_out"`, `agent_count` |
| `orchestrator.stream_dispatch` | `agent.name`, `messages.count` |
| HTTP middleware | `http.method`, `http.target`, `http.url`, `http.status_code` |

When adding new spans, place them **inside** these parents so the trace tree
stays readable.

---

## Template — Instrument a Function

```python
from __future__ import annotations

import logging

from opentelemetry import trace

from mangomas.telemetry import get_tracer

logger = logging.getLogger(__name__)
_tracer = get_tracer(__name__)


async def do_thing(item_id: str) -> str:
    with _tracer.start_as_current_span("component.do_thing") as span:
        span.set_attribute("item.id", item_id)
        logger.info(
            "Processing item",
            extra={"item_id": item_id, "phase": "start"},
        )
        try:
            result = await _work(item_id)
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
            raise
        span.set_attribute("result.size", len(result))
        return result
```

---

## Workflow

1. Pick a span name that follows `<layer>.<component>.<action>`.
2. Add `_tracer = get_tracer(__name__)` at module top if not present.
3. Wrap the unit of work in `with _tracer.start_as_current_span(name) as span:`.
4. Set attributes for the inputs/outputs that would help debugging.
5. Convert any `print` or `logger.info(f"...{x}...")` to `logger.info(msg, extra={"x": x})`.
6. Run tests; verify the span appears in the OTel console exporter when running with `RUN_LMSTUDIO=1`.

---

## Constraints

- DO NOT create a new `TracerProvider` — always go through `get_tracer`.
- DO NOT log via `print()` or `f"...{secret}..."` strings.
- DO NOT swallow exceptions inside spans without `span.record_exception(exc)`.
- DO NOT set sensitive values as span attributes (API keys, raw bodies).
- DO NOT create a span for every trivial helper — instrument at meaningful boundaries (adapter calls, agent execution, pipeline stages).

---

## Diagnosing Failures

1. Span missing from console output → confirm `configure_telemetry()` has been called (it auto-runs on first `get_tracer()`).
2. Duplicate spans → you wrapped a function that already had a span; remove one.
3. `trace_id`/`span_id` shows `-` in logs → outside any active span; either widen the wrapping span or skip.
4. JSON log shape unexpected → `JsonFormatter` includes only fields in `extra={}` plus standard `LogRecord` fields; do not rely on `args`.
5. Correlation ID is `-` → `CorrelationFilter` did not see a ContextVar value; ensure `AccessLogMiddleware` or `set_correlation_id(...)` ran before the log line.
