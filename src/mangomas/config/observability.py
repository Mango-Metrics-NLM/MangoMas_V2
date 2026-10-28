"""Logging and telemetry settings (`MANGOMAS_LOG__*`, `MANGOMAS_TELEMETRY__*`)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

DEFAULT_LOG_FORMAT: Literal["text", "json"] = "text"


class LogSettings(BaseModel):
    """Logging format configuration.

    ``body_truncate`` (``MANGOMAS_LOG__BODY_TRUNCATE``) was removed: it was
    declared and documented but read by nothing, so a value set for it changed
    no log line (spec-0031 S12). ``LogSettings`` is a ``BaseModel`` under a
    ``BaseSettings`` root, so a still-exported variable is ignored, not rejected.
    """

    format: Literal["text", "json"] = DEFAULT_LOG_FORMAT


DEFAULT_TELEMETRY_EXPORTER: Literal["console", "gcp"] = "console"


# Opt-in metrics pipeline (ADR-0013). Default False → no MeterProvider installed,
# so the global provider stays the OTel no-op and recording is byte-identical.
DEFAULT_TELEMETRY_METRICS_ENABLED: bool = False


class TelemetrySettings(BaseModel):
    """OpenTelemetry exporter selection.

    ``exporter`` chooses the application span exporter: ``console`` (default,
    built-in) or ``gcp`` (Cloud Trace via the optional ``gcp`` extra). Absent
    from the environment → ``console``, identical to prior behaviour.

    ``metrics_enabled`` gates the opt-in metrics pipeline (ADR-0013); the same
    ``exporter`` token selects the metric exporter. Default ``False`` installs no
    ``MeterProvider``, so the global provider stays the OTel no-op.
    """

    exporter: Literal["console", "gcp"] = DEFAULT_TELEMETRY_EXPORTER
    metrics_enabled: bool = DEFAULT_TELEMETRY_METRICS_ENABLED
