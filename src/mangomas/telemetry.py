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

from opentelemetry import metrics, trace
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from mangomas.correlation import CorrelationFilter
from mangomas.errors import ConfigError

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
    metrics_configured: bool = False


_state = _TelemetryState()
_lock = Lock()
_metrics_lock = Lock()
# Cache of dedicated scoped tracers keyed by (namespace, exporter) so repeated
# build_orchestrator calls reuse one TracerProvider/SpanProcessor instead of
# leaking a new one each time (matters most for the gcp Cloud Trace exporter).
_scoped_tracers: dict[tuple[str, str], trace.Tracer] = {}
_scoped_lock = Lock()
logger = logging.getLogger(__name__)


_GCP_TRACE_INSTALL_HINT = (
    "Cloud Trace exporter is not installed. Install the optional extra: pip install 'mangomas[gcp]'"
)

# Exporter selection tokens. ``console`` is the built-in default; ``gcp`` routes
# spans to Cloud Trace via the optional ``gcp`` extra. ``inherit`` is a
# harness-only token meaning "reuse the global application exporter".
EXPORTER_CONSOLE = "console"
EXPORTER_GCP = "gcp"
EXPORTER_INHERIT = "inherit"


def _lazy_cloud_trace_exporter() -> Any:  # pragma: no cover - requires gcp extra
    """Import and construct the Cloud Trace span exporter lazily.

    Excluded from coverage because the success path requires the optional
    ``gcp`` extra; unit tests monkeypatch this helper, and the gated
    ``RUN_GCP_TRACE=1`` suite exercises the real SDK.
    """
    try:
        # Lazy: the exporter is an optional extra; importing at module load
        # would force the dependency on every user.
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(_GCP_TRACE_INSTALL_HINT) from exc
    return CloudTraceSpanExporter()


_VALID_APP_EXPORTERS: frozenset[str] = frozenset({EXPORTER_CONSOLE, EXPORTER_GCP})


def _build_span_exporter(exporter: str) -> Any:
    """Return a span exporter for the selected *exporter* token.

    Shared by :func:`configure_telemetry` (application spans) and
    :func:`build_scoped_tracer` (harness spans) so exporter selection lives in
    one place. An unknown token raises :class:`ConfigError` rather than silently
    falling back to console — consistent with the registry ``UnknownProvider``
    contract. (``inherit`` is resolved in :func:`build_scoped_tracer` and never
    reaches here.)
    """
    if exporter == EXPORTER_GCP:
        return _lazy_cloud_trace_exporter()
    if exporter == EXPORTER_CONSOLE:
        return ConsoleSpanExporter()
    raise ConfigError(
        f"Unknown telemetry exporter {exporter!r}; expected one of {sorted(_VALID_APP_EXPORTERS)}."
    )


_GCP_MONITORING_INSTALL_HINT = (
    "Cloud Monitoring metric exporter is not installed. "
    "Install: pip install opentelemetry-exporter-gcp-monitoring"
)


def _lazy_cloud_monitoring_exporter() -> Any:  # pragma: no cover - requires gcp monitoring extra
    """Import and construct the Cloud Monitoring metric exporter lazily.

    Excluded from coverage because the success path requires an optional GCP
    exporter that the base ``gcp`` extra (Cloud Trace only) does not install; the
    metric-reader unit tests inject an ``InMemoryMetricReader`` instead.
    """
    try:
        from opentelemetry.exporter.cloud_monitoring import (  # noqa: PLC0415
            CloudMonitoringMetricsExporter,
        )
    except ImportError as exc:
        raise ImportError(_GCP_MONITORING_INSTALL_HINT) from exc
    return CloudMonitoringMetricsExporter()


def _build_metric_reader(exporter: str) -> Any:
    """Return a periodic metric reader for the selected *exporter* token.

    Mirrors :func:`_build_span_exporter` so metric-exporter selection reuses the
    same ``console``/``gcp`` tokens. An unknown token raises :class:`ConfigError`.
    """
    if exporter == EXPORTER_GCP:
        return PeriodicExportingMetricReader(_lazy_cloud_monitoring_exporter())
    if exporter == EXPORTER_CONSOLE:
        return PeriodicExportingMetricReader(ConsoleMetricExporter())
    raise ConfigError(
        f"Unknown telemetry exporter {exporter!r}; expected one of {sorted(_VALID_APP_EXPORTERS)}."
    )


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
        provider.add_span_processor(SimpleSpanProcessor(_build_span_exporter(exporter)))
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


def configure_metrics(
    service_name: str = "mangomas",
    exporter: str = EXPORTER_CONSOLE,
    *,
    enabled: bool = False,
    reader: Any = None,
) -> None:
    """Idempotently install a global ``MeterProvider`` when *enabled*.

    Default-OFF: when *enabled* is ``False`` this is a no-op, so the global
    provider stays the OTel no-op and every instrument ``.add()``/``.record()``
    call costs nothing. A *reader* may be injected (tests pass an
    ``InMemoryMetricReader``); otherwise a periodic reader is built from the
    *exporter* token. Safe to call multiple times.
    """
    if not enabled:
        return
    with _metrics_lock:
        if _state.metrics_configured:
            return
        metric_reader = reader if reader is not None else _build_metric_reader(exporter)
        provider = MeterProvider(
            resource=Resource.create({"service.name": service_name}),
            metric_readers=[metric_reader],
        )
        metrics.set_meter_provider(provider)
        logger.debug(
            "Metrics configured",
            extra={"event": "metrics_configured", "exporter": exporter},
        )
        _state.metrics_configured = True


def get_meter(name: str = "mangomas") -> metrics.Meter:
    """Return a meter from the global ``MeterProvider``.

    Returns a no-op meter until :func:`configure_metrics` installs a real
    provider, so callers can record unconditionally.
    """
    return metrics.get_meter(name)


def build_scoped_tracer(namespace: str, *, exporter: str = EXPORTER_INHERIT) -> trace.Tracer:
    """Return a tracer for *namespace*, optionally on a dedicated exporter.

    When *exporter* is ``inherit`` (default) the tracer uses the global
    application provider — identical to :func:`get_tracer`, so the shared
    exporter is reused and behaviour is unchanged. When *exporter* is
    ``console``/``gcp`` a dedicated :class:`TracerProvider` with its own span
    processor is built, so spans from this tracer are routed independently of
    application spans (the global provider is shared, so a namespace alone
    cannot reroute them).
    """
    if exporter == EXPORTER_INHERIT:
        return get_tracer(namespace)
    key = (namespace, exporter)
    with _scoped_lock:
        cached = _scoped_tracers.get(key)
        if cached is not None:
            return cached
        # Ensure logging + the global provider exist first (idempotent).
        if not _state.configured:
            configure_telemetry()
        provider = TracerProvider(resource=Resource.create({"service.name": namespace}))
        provider.add_span_processor(SimpleSpanProcessor(_build_span_exporter(exporter)))
        logger.debug(
            "Scoped tracer built",
            extra={"event": "scoped_tracer_built", "namespace": namespace, "exporter": exporter},
        )
        tracer = provider.get_tracer(namespace)
        _scoped_tracers[key] = tracer
        return tracer
