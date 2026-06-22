"""Span-exporter selection for OpenTelemetry tracing.

Decouples *which* span exporter is used from the telemetry bootstrap in
:mod:`mangomas.telemetry`. The selection reuses the generic
:class:`mangomas.registry.Registry` so adding a new exporter is a one-line
``register`` call, and optional cloud SDKs are imported lazily so the module
stays importable without the ``otlp`` / ``gcp-trace`` extras installed.

Two public entry points:

``resolve_exporter(cfg)``
    Return a configured :class:`SpanExporter` for the application-wide provider.

``build_harness_tracer(harness_cfg)``
    Return a tracer backed by a *dedicated, isolated* provider so harness spans
    can be routed to a different backend than application spans. The provider is
    never promoted via ``trace.set_tracer_provider`` — this is what keeps the
    harness exporter from leaking into global state (and keeps tests isolated).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeAlias, cast

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)

from mangomas.config import HarnessSettings, TelemetrySettings
from mangomas.errors import ConfigError
from mangomas.registry import Registry

if TYPE_CHECKING:  # pragma: no cover
    from opentelemetry.sdk.trace import SpanProcessor

logger = logging.getLogger(__name__)

# A factory turns a validated ``TelemetrySettings`` into a concrete exporter.
SpanExporterFactory: TypeAlias = Callable[[TelemetrySettings], SpanExporter]

# Install hints surfaced when an optional exporter SDK is missing.
_OTLP_INSTALL_HINT = (
    "OTLP exporter is not installed. Install the optional extra: pip install 'mangomas[otlp]'"
)
_GCP_TRACE_INSTALL_HINT = (
    "Cloud Trace exporter is not installed. Install the optional extra: "
    "pip install 'mangomas[gcp-trace]'"
)


def _console_exporter_factory(cfg: TelemetrySettings) -> SpanExporter:  # noqa: ARG001
    """Return the in-process console exporter (no SDK extra required)."""
    return ConsoleSpanExporter()


def _otlp_exporter_factory(cfg: TelemetrySettings) -> SpanExporter:
    """Return an OTLP/gRPC exporter targeting ``cfg.otlp_endpoint``.

    The OTLP SDK is imported lazily so this module is importable without the
    ``otlp`` extra.
    """
    if not cfg.otlp_endpoint:
        raise ConfigError("MANGOMAS_TELEMETRY__OTLP_ENDPOINT is required when exporter='otlp'.")
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # noqa: PLC0415
            OTLPSpanExporter,
        )
    except ImportError as exc:  # pragma: no cover -- requires the otlp extra absent
        raise ImportError(_OTLP_INSTALL_HINT) from exc
    # pragma: no cover -- construction requires the otlp extra installed
    return cast("SpanExporter", OTLPSpanExporter(endpoint=cfg.otlp_endpoint))  # pragma: no cover


def _gcp_trace_exporter_factory(cfg: TelemetrySettings) -> SpanExporter:
    """Return a Cloud Trace exporter for ``cfg.gcp_project_id`` (ADC auth).

    The Cloud Trace SDK is imported lazily so this module is importable without
    the ``gcp-trace`` extra.
    """
    if not cfg.gcp_project_id:
        raise ConfigError("MANGOMAS_TELEMETRY__GCP_PROJECT_ID is required when exporter='gcp'.")
    try:
        from opentelemetry.exporter.cloud_trace import (  # noqa: PLC0415
            CloudTraceSpanExporter,
        )
    except ImportError as exc:  # pragma: no cover -- requires the gcp-trace extra absent
        raise ImportError(_GCP_TRACE_INSTALL_HINT) from exc
    # pragma: no cover -- construction requires the gcp-trace extra installed
    return cast(  # pragma: no cover
        "SpanExporter", CloudTraceSpanExporter(project_id=cfg.gcp_project_id)
    )


# Registry of exporter factories. ``console`` is registered eagerly (no extra);
# ``otlp``/``gcp`` are registered lazily on first selection in ``resolve_exporter``
# so importing this module never pulls an optional cloud SDK.
exporter_registry: Registry[SpanExporterFactory] = Registry("span_exporter")
exporter_registry.register("console", _console_exporter_factory)

# Lazy-registration table: exporter name → factory. Mirrors the
# register-on-selection idiom used for cloud secrets in ``composition.py``.
_LAZY_FACTORIES: dict[str, SpanExporterFactory] = {
    "otlp": _otlp_exporter_factory,
    "gcp": _gcp_trace_exporter_factory,
}


def resolve_exporter(cfg: TelemetrySettings) -> SpanExporter:
    """Build the :class:`SpanExporter` selected by ``cfg.exporter``.

    Registers the ``otlp``/``gcp`` factory on first use so optional SDKs stay
    unimported until actually selected. Raises :class:`ConfigError` for a
    selected-but-misconfigured exporter (e.g. missing endpoint/project).
    """
    name = cfg.exporter
    if name in _LAZY_FACTORIES and name not in exporter_registry.available():
        exporter_registry.register(name, _LAZY_FACTORIES[name])
        logger.debug("Lazily registered span exporter factory", extra={"exporter": name})
    factory = exporter_registry.get(name)
    exporter = factory(cfg)
    logger.debug("Resolved span exporter", extra={"exporter": name})
    return exporter


def make_span_processor(exporter: SpanExporter, *, exporter_name: str) -> SpanProcessor:
    """Pick a span processor for *exporter*.

    ``console`` uses :class:`SimpleSpanProcessor` (synchronous → deterministic in
    tests and local runs); every other exporter uses :class:`BatchSpanProcessor`
    (batched export suited to production network backends).
    """
    if exporter_name == "console":
        return SimpleSpanProcessor(exporter)
    return BatchSpanProcessor(exporter)


def build_harness_tracer(harness_cfg: HarnessSettings) -> trace.Tracer:
    """Return a tracer on a dedicated provider routed to a separate exporter.

    Only called when ``harness_cfg.metrics_exporter`` is set. The provider is
    *local* — it is never passed to ``trace.set_tracer_provider`` — so harness
    spans reach an isolated backend without mutating the global provider or
    leaking state across tests/processes.
    """
    if harness_cfg.metrics_exporter is None:  # pragma: no cover -- guarded by caller
        raise ConfigError("build_harness_tracer requires harness.metrics_exporter to be set.")
    tele = TelemetrySettings(
        exporter=harness_cfg.metrics_exporter,
        otlp_endpoint=harness_cfg.otlp_endpoint,
        gcp_project_id=harness_cfg.gcp_project_id,
        service_name=harness_cfg.metrics_namespace,
    )
    exporter = resolve_exporter(tele)
    provider = TracerProvider(
        resource=Resource.create({"service.name": harness_cfg.metrics_namespace})
    )
    provider.add_span_processor(make_span_processor(exporter, exporter_name=tele.exporter))
    logger.debug(
        "Built dedicated harness tracer",
        extra={
            "exporter": tele.exporter,
            "metrics_namespace": harness_cfg.metrics_namespace,
        },
    )
    return provider.get_tracer(harness_cfg.metrics_namespace)
