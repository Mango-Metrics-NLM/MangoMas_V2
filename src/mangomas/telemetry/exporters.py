"""Exporter and metric-reader selection — the whole seam, in one file.

Both `_build_span_exporter` and `_build_metric_reader` share the token
vocabulary (`EXPORTER_*`, `_VALID_APP_EXPORTERS`) and the same `ConfigError`
shape, so cutting this package by signal type (tracing vs metrics) would have
split the vocabulary across two modules and forced either duplication or a
third module to hold it. Cutting by dependency layer instead keeps the seam
whole.

Consequence worth stating: this is the **only** module in the package allowed
to name a cloud SDK. "No top-level `opentelemetry.exporter.*` import" is
therefore a one-file audit, and `tests/test_telemetry.py` asserts it across the
package rather than trusting review.

Consumers call these through the module object (`exporters._build_span_exporter(...)`),
never via `from ... import`. That is what lets a single `monkeypatch.setattr`
on this module reach `tracing`, `meters` and `scoped` at once — a name bound at
import time in each consumer would need three separate patches, and would fail
silently in the one place that does not assert loudly.
"""

from __future__ import annotations

from typing import Any

from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.trace.export import ConsoleSpanExporter

from mangomas.errors import ConfigError

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
    # The optional exporter package may not ship precise constructor metadata in
    # every release, so this call site stays intentionally isolated in one seam.
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
