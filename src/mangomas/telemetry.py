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
from typing import TYPE_CHECKING, Any

from opentelemetry import trace
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from mangomas.correlation import CorrelationFilter

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.config import TelemetrySettings

logger = logging.getLogger(__name__)

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
        log_dict.update(
            {
                key: value
                for key, value in record.__dict__.items()
                if key not in _STANDARD_LOG_RECORD_ATTRS
            }
        )
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
    telemetry: TelemetrySettings | None = None,
) -> None:
    """Idempotently configure logging + tracing.

    When *telemetry* is ``None`` the span exporter defaults to the in-process
    console exporter — byte-identical to the historical behaviour. When a
    :class:`~mangomas.config.TelemetrySettings` is supplied, the configured
    exporter (``console``/``otlp``/``gcp``) is resolved and attached instead.

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
        if telemetry is None:
            # Default-OFF: identical to the historical console-only behaviour.
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        else:
            # Local import avoids a config import cycle at module load.
            from mangomas.telemetry_exporters import (  # noqa: PLC0415
                make_span_processor,
                resolve_exporter,
            )

            exporter = resolve_exporter(telemetry)
            provider.add_span_processor(
                make_span_processor(exporter, exporter_name=telemetry.exporter)
            )
            logger.info(
                "Span exporter configured",
                extra={"exporter": telemetry.exporter, "service_name": service_name},
            )
        trace.set_tracer_provider(provider)

        _state.configured = True


def get_tracer(name: str = "mangomas") -> trace.Tracer:
    """Return a tracer for *name*.

    Does **not** auto-configure telemetry. OpenTelemetry's ``get_tracer``
    returns a ``ProxyTracer`` that transparently delegates to the real provider
    once it is registered, so module-level ``get_tracer(__name__)`` calls are
    safe before :func:`configure_telemetry` runs. Auto-configuring here would
    lock in the default (console) exporter and silently ignore the real
    configuration applied later at the application entry point (the lifespan /
    CLI), because :func:`configure_telemetry` is idempotent.
    """
    return trace.get_tracer(name)


def flush_telemetry() -> None:
    """Force-flush buffered spans on the global provider.

    Call from an application entry point's shutdown path (e.g. the FastAPI
    lifespan ``finally``) so spans queued in a ``BatchSpanProcessor`` are
    exported before the process exits a graceful shutdown / scale-down. Unlike
    a full ``shutdown``, this leaves the provider usable, so it is safe to call
    repeatedly and does not tear down a process-global singleton.
    """
    provider = trace.get_tracer_provider()
    force_flush = getattr(provider, "force_flush", None)
    if callable(force_flush):
        force_flush()
