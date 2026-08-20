"""OpenTelemetry configuration: tracing, metrics, and structured logging.

This module is a **permanent re-export facade** (ADR-0019). `telemetry.py` was
337 lines mixing five concerns; it is now a package cut by *dependency layer*
rather than by telemetry signal:

    _state      process-global singletons (base; imports nothing local)
    logs        JSON formatter + trace-context filter (pure leaf)
    exporters   the whole exporter/reader selection seam (base)
    tracing     configure_telemetry / get_tracer      -> _state, logs, exporters
    meters      configure_metrics / get_meter         -> _state, exporters
    scoped      build_scoped_tracer                   -> _state, exporters, tracing

Cutting by signal would have split the shared exporter-token vocabulary across
tracing and metrics; cutting by layer keeps it whole in one file.

Every public name stays importable from `mangomas.telemetry` exactly as before.
The facade additionally re-exports the **private** names that tests and other
modules reach for (`_state`, `_scoped_tracers`, the two `_build_*` selectors and
the two `_lazy_*` helpers) — those are load-bearing, not incidental:
`tests/test_telemetry.py` clears `_scoped_tracers` between tests, and a facade
that rebuilt it rather than re-exporting the same object would leave stale
cached providers behind and fail order-dependently.

`tests/test_import_compat.py` asserts identity for both the public and the
private surface.
"""

from __future__ import annotations

from mangomas.telemetry import exporters as exporters
from mangomas.telemetry import logs as logs
from mangomas.telemetry import meters as meters
from mangomas.telemetry import scoped as scoped
from mangomas.telemetry import tracing as tracing
from mangomas.telemetry._state import _lock as _lock
from mangomas.telemetry._state import _metrics_lock as _metrics_lock
from mangomas.telemetry._state import _scoped_lock as _scoped_lock
from mangomas.telemetry._state import _scoped_tracers as _scoped_tracers
from mangomas.telemetry._state import _state as _state
from mangomas.telemetry._state import _TelemetryState as _TelemetryState
from mangomas.telemetry.exporters import (
    _GCP_MONITORING_INSTALL_HINT as _GCP_MONITORING_INSTALL_HINT,
)
from mangomas.telemetry.exporters import _GCP_TRACE_INSTALL_HINT as _GCP_TRACE_INSTALL_HINT
from mangomas.telemetry.exporters import _VALID_APP_EXPORTERS as _VALID_APP_EXPORTERS
from mangomas.telemetry.exporters import EXPORTER_CONSOLE as EXPORTER_CONSOLE
from mangomas.telemetry.exporters import EXPORTER_GCP as EXPORTER_GCP
from mangomas.telemetry.exporters import EXPORTER_INHERIT as EXPORTER_INHERIT
from mangomas.telemetry.exporters import _build_metric_reader as _build_metric_reader
from mangomas.telemetry.exporters import _build_span_exporter as _build_span_exporter
from mangomas.telemetry.exporters import (
    _lazy_cloud_monitoring_exporter as _lazy_cloud_monitoring_exporter,
)
from mangomas.telemetry.exporters import _lazy_cloud_trace_exporter as _lazy_cloud_trace_exporter
from mangomas.telemetry.logs import _STANDARD_LOG_RECORD_ATTRS as _STANDARD_LOG_RECORD_ATTRS
from mangomas.telemetry.logs import JsonFormatter as JsonFormatter
from mangomas.telemetry.logs import TraceContextFilter as TraceContextFilter
from mangomas.telemetry.meters import configure_metrics as configure_metrics
from mangomas.telemetry.meters import get_meter as get_meter
from mangomas.telemetry.scoped import build_scoped_tracer as build_scoped_tracer
from mangomas.telemetry.tracing import configure_telemetry as configure_telemetry
from mangomas.telemetry.tracing import get_tracer as get_tracer

__all__ = [
    "EXPORTER_CONSOLE",
    "EXPORTER_GCP",
    "EXPORTER_INHERIT",
    "JsonFormatter",
    "TraceContextFilter",
    "build_scoped_tracer",
    "configure_metrics",
    "configure_telemetry",
    "get_meter",
    "get_tracer",
]
