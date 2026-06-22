"""Tests for ``TelemetrySettings`` defaults and env overrides."""

from __future__ import annotations

import pytest

from mangomas.config import (
    DEFAULT_TELEMETRY_EXPORTER,
    DEFAULT_TELEMETRY_GCP_PROJECT_ID,
    DEFAULT_TELEMETRY_OTLP_ENDPOINT,
    DEFAULT_TELEMETRY_SERVICE_NAME,
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
