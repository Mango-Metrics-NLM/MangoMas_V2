"""Application tracing: the span-provider bootstrap and tracer accessor."""

from __future__ import annotations

import logging

from opentelemetry import trace
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from mangomas.correlation import CorrelationFilter
from mangomas.telemetry import exporters
from mangomas.telemetry._state import _lock, _state
from mangomas.telemetry.exporters import EXPORTER_CONSOLE
from mangomas.telemetry.logs import JsonFormatter, TraceContextFilter

# Bound to the package name rather than ``__name__`` so the ``logger`` field in
# a JSON log record reads ``mangomas.telemetry``, exactly as it did before the
# spec-0015 split. A refactor that promises no behaviour change should not
# quietly rename a field operators may filter on.
logger = logging.getLogger("mangomas.telemetry")


def configure_telemetry(
    service_name: str = "mangomas",
    log_level: str = "INFO",
    log_format: str = "text",
    exporter: str = EXPORTER_CONSOLE,
) -> None:
    """Idempotently configure logging + tracing.

    *exporter* selects the application span exporter (``console`` default, or
    ``gcp`` for Cloud Trace). Subsequent calls are no-ops; safe to call from
    tests.
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
        provider.add_span_processor(SimpleSpanProcessor(exporters._build_span_exporter(exporter)))  # noqa: SLF001 -- resolved through the module object on purpose: it is what
        # lets one monkeypatch on `telemetry.exporters` reach tracing, meters and
        # scoped at once. A `from ... import` here would bind the name locally and
        # need three separate patches. The selectors stay underscore-private because
        # ADR-0009, ADR-0013 and the mango-observability skill cite them by that name.
        trace.set_tracer_provider(provider)

        logger.debug(
            "Telemetry configured",
            extra={"event": "telemetry_configured", "exporter": exporter},
        )
        _state.configured = True


def get_tracer(name: str = "mangomas") -> trace.Tracer:
    """Return a tracer; configures telemetry with defaults on first use."""
    if not _state.configured:
        configure_telemetry()
    return trace.get_tracer(name)
