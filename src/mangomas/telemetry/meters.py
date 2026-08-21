"""Metrics: the opt-in `MeterProvider` bootstrap and meter accessor (ADR-0013).

Named `meters` rather than `metrics` because `src/mangomas/metrics.py` already
exists and imports from this package; two modules named `metrics` would be a
permanent reader trap and would muddle the `metrics` coverage-floor label.
"""

from __future__ import annotations

import logging
from typing import Any

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource

from mangomas.telemetry import exporters
from mangomas.telemetry._state import _metrics_lock, _state
from mangomas.telemetry.exporters import EXPORTER_CONSOLE

# See the note in ``tracing.py``: the logger name is pinned to the package.
logger = logging.getLogger("mangomas.telemetry")


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
        metric_reader = reader if reader is not None else exporters._build_metric_reader(exporter)  # noqa: SLF001 -- resolved through the module object on purpose: it is what
        # lets one monkeypatch on `telemetry.exporters` reach tracing, meters and
        # scoped at once. A `from ... import` here would bind the name locally and
        # need three separate patches. The selectors stay underscore-private because
        # ADR-0009, ADR-0013 and the mango-observability skill cite them by that name.
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
