"""OpenTelemetry → stdout telemetry bootstrap.

Kept intentionally minimal: a console span exporter and a module-level tracer.
Swap exporters for Cloud Trace when migrating to GCP.

``configure_telemetry`` is idempotent; safe to call multiple times (e.g., from
tests).  Uses ``_TelemetryState`` instead of a ``global`` flag to avoid ruff
PLW0603 ("Using the global statement to update '…' is discouraged").
"""

from __future__ import annotations

import json
import logging
from threading import Lock
from typing import Any

from opentelemetry import trace
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from mangomas.correlation import CorrelationFilter

# Standard LogRecord attributes that we do NOT forward into the JSON envelope.
_STANDARD_LOG_RECORD_ATTRS: frozenset[str] = frozenset(
    {
        "args",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


class JsonFormatter(logging.Formatter):
    """Structured JSON log formatter (GCP Cloud Logging compatible).

    Enabled when ``Settings.log.format == "json"``.
    """

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        log_dict: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.message,
        }
        if record.exc_info:
            log_dict["exception"] = self.formatException(record.exc_info)
        # Forward extra fields added via ``logger.info(..., extra={...})``.
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_RECORD_ATTRS:
                log_dict[key] = value
        return json.dumps(log_dict, ensure_ascii=False, default=str)


class TraceContextFilter(logging.Filter):
    """Logging filter that injects the current OTel trace/span IDs into log records.

    Install once via ``logging.getLogger().addFilter(TraceContextFilter())``.
    When no active span exists the fields are set to empty strings.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        span = trace.get_current_span()
        span_ctx = span.get_span_context()
        if span_ctx.is_valid:
            record.trace_id = format(span_ctx.trace_id, "032x")
            record.span_id = format(span_ctx.span_id, "016x")
        else:
            record.trace_id = ""
            record.span_id = ""
        return True


class _TelemetryState:
    """Mutable singleton that tracks idempotency — avoids PLW0603."""

    configured: bool = False


_state = _TelemetryState()
_lock = Lock()


def configure_telemetry(
    service_name: str = "mangomas",
    log_level: str = "INFO",
    log_format: str = "text",
) -> None:
    """Idempotently configure logging + tracing to stdout.

    Subsequent calls are no-ops; safe to call from tests.
    """
    with _lock:
        if _state.configured:
            return

        handler = logging.StreamHandler()
        if log_format == "json":
            handler.setFormatter(JsonFormatter())
        else:
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s :: %(message)s")
            )
        # Filters attached to handlers run for every propagated record;
        # filters attached to the root logger do NOT run for records emitted
        # by child loggers, so trace_id/span_id must be injected here.
        handler.addFilter(TraceContextFilter())
        handler.addFilter(CorrelationFilter())

        logging.basicConfig(
            level=getattr(logging, log_level.upper(), logging.INFO),
            handlers=[handler],
            force=True,
        )

        # Install W3C TraceContext propagator for distributed tracing.
        set_global_textmap(TraceContextTextMapPropagator())

        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)

        _state.configured = True


def get_tracer(name: str = "mangomas") -> trace.Tracer:
    """Return a tracer; configures telemetry with defaults on first use."""
    if not _state.configured:
        configure_telemetry()
    return trace.get_tracer(name)
