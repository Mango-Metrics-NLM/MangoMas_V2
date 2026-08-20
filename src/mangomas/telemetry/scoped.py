"""Harness-scoped tracers (ADR-0021).

Its own module because it is the only harness-facing surface (`composition.py`
is the sole `src/` consumer), the only cache owner, and the only place where the
exporter is chosen per namespace rather than globally.
"""

from __future__ import annotations

import logging

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from mangomas.telemetry import exporters, tracing
from mangomas.telemetry._state import _scoped_lock, _scoped_tracers, _state
from mangomas.telemetry.exporters import EXPORTER_INHERIT

# See the note in ``tracing.py``: the logger name is pinned to the package.
logger = logging.getLogger("mangomas.telemetry")


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
        return tracing.get_tracer(namespace)
    key = (namespace, exporter)
    with _scoped_lock:
        cached = _scoped_tracers.get(key)
        if cached is not None:
            return cached
        # Ensure logging + the global provider exist first (idempotent).
        if not _state.configured:
            tracing.configure_telemetry()
        provider = TracerProvider(resource=Resource.create({"service.name": namespace}))
        provider.add_span_processor(SimpleSpanProcessor(exporters._build_span_exporter(exporter)))  # noqa: SLF001 -- resolved through the module object on purpose: it is what
        # lets one monkeypatch on `telemetry.exporters` reach tracing, meters and
        # scoped at once. A `from ... import` here would bind the name locally and
        # need three separate patches. The selectors stay underscore-private because
        # ADR-0009, ADR-0013 and the mango-observability skill cite them by that name.
        logger.debug(
            "Scoped tracer built",
            extra={"event": "scoped_tracer_built", "namespace": namespace, "exporter": exporter},
        )
        tracer = provider.get_tracer(namespace)
        _scoped_tracers[key] = tracer
        return tracer
