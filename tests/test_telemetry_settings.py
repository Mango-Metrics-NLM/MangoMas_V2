"""Tests for ``TelemetrySettings`` defaults and env overrides."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mangomas.config import (
    DEFAULT_TELEMETRY_EXPORTER,
    DEFAULT_TELEMETRY_GCP_PROJECT_ID,
    DEFAULT_TELEMETRY_OTLP_ENDPOINT,
    DEFAULT_TELEMETRY_SERVICE_NAME,
    HarnessSettings,
    Settings,
    TelemetrySettings,
    get_settings,
)


def test_telemetry_defaults_match_constants() -> None:
    s = TelemetrySettings()
    assert s.exporter == DEFAULT_TELEMETRY_EXPORTER
    assert s.otlp_endpoint == DEFAULT_TELEMETRY_OTLP_ENDPOINT
    assert s.gcp_project_id == DEFAULT_TELEMETRY_GCP_PROJECT_ID
    assert s.service_name == DEFAULT_TELEMETRY_SERVICE_NAME


def test_settings_includes_telemetry_console_default() -> None:
    """A bare ``Settings()`` carries a console-exporter TelemetrySettings."""
    s = Settings()
    assert isinstance(s.telemetry, TelemetrySettings)
    assert s.telemetry.exporter == "console"


def test_telemetry_env_override_exporter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_TELEMETRY__EXPORTER", "otlp")
    monkeypatch.setenv("MANGOMAS_TELEMETRY__OTLP_ENDPOINT", "http://collector:4317")
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.telemetry.exporter == "otlp"
        assert s.telemetry.otlp_endpoint == "http://collector:4317"
    finally:
        get_settings.cache_clear()


def test_telemetry_env_override_gcp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_TELEMETRY__EXPORTER", "gcp")
    monkeypatch.setenv("MANGOMAS_TELEMETRY__GCP_PROJECT_ID", "proj-xyz")
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.telemetry.exporter == "gcp"
        assert s.telemetry.gcp_project_id == "proj-xyz"
    finally:
        get_settings.cache_clear()


# ── Load-time validation (fail-fast) ──────────────────────────────────────────


def test_telemetry_otlp_requires_endpoint() -> None:
    with pytest.raises(ValidationError, match="otlp_endpoint is required"):
        TelemetrySettings(exporter="otlp")


def test_telemetry_gcp_requires_project() -> None:
    with pytest.raises(ValidationError, match="gcp_project_id is required"):
        TelemetrySettings(exporter="gcp")


def test_telemetry_console_needs_no_extra_fields() -> None:
    # The console default must validate cleanly with no endpoint/project.
    assert TelemetrySettings(exporter="console").exporter == "console"


def test_harness_metrics_exporter_otlp_requires_endpoint() -> None:
    with pytest.raises(ValidationError, match="otlp_endpoint is required"):
        HarnessSettings(enabled=True, metrics_exporter="otlp")


def test_harness_metrics_exporter_gcp_requires_project() -> None:
    with pytest.raises(ValidationError, match="gcp_project_id is required"):
        HarnessSettings(enabled=True, metrics_exporter="gcp")


def test_harness_metrics_exporter_none_is_valid() -> None:
    # The default (no routing) needs no endpoint/project.
    assert HarnessSettings(enabled=True).metrics_exporter is None
