"""Tests for ``HarnessSettings`` and its integration with the composition root."""

from __future__ import annotations

import pytest

from mangomas.config import (
    DEFAULT_HARNESS_ENABLED,
    DEFAULT_HARNESS_GCP_PROJECT_ID,
    DEFAULT_HARNESS_HOOK_LOG_LEVEL,
    DEFAULT_HARNESS_METRICS_EXPORTER,
    DEFAULT_HARNESS_METRICS_NAMESPACE,
    DEFAULT_HARNESS_OTLP_ENDPOINT,
    HarnessSettings,
    Settings,
    get_settings,
)


def test_harness_defaults_match_constants() -> None:
    """``HarnessSettings()`` defaults exactly to the documented module constants."""
    s = HarnessSettings()
    assert s.enabled == DEFAULT_HARNESS_ENABLED
    assert s.metrics_namespace == DEFAULT_HARNESS_METRICS_NAMESPACE
    assert s.hook_log_level == DEFAULT_HARNESS_HOOK_LOG_LEVEL
    assert s.metrics_exporter == DEFAULT_HARNESS_METRICS_EXPORTER
    assert s.otlp_endpoint == DEFAULT_HARNESS_OTLP_ENDPOINT
    assert s.gcp_project_id == DEFAULT_HARNESS_GCP_PROJECT_ID


def test_harness_metrics_exporter_defaults_off() -> None:
    """The dedicated harness exporter is opt-in (``None`` by default)."""
    assert HarnessSettings().metrics_exporter is None


def test_harness_env_override_metrics_exporter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_HARNESS__METRICS_EXPORTER", "otlp")
    monkeypatch.setenv("MANGOMAS_HARNESS__OTLP_ENDPOINT", "http://collector:4317")
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.harness.metrics_exporter == "otlp"
        assert s.harness.otlp_endpoint == "http://collector:4317"
    finally:
        get_settings.cache_clear()


def test_settings_includes_harness_with_safe_default() -> None:
    """``Settings()`` constructed bare carries a disabled HarnessSettings."""
    s = Settings()
    assert isinstance(s.harness, HarnessSettings)
    assert s.harness.enabled is False


def test_harness_env_override_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """``MANGOMAS_HARNESS__ENABLED=true`` flips the flag without breaking other groups."""
    monkeypatch.setenv("MANGOMAS_HARNESS__ENABLED", "true")
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.harness.enabled is True
        # Other groups remain at their defaults — backward-compat guard
        assert s.harness.metrics_namespace == DEFAULT_HARNESS_METRICS_NAMESPACE
    finally:
        get_settings.cache_clear()


def test_harness_env_override_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_HARNESS__METRICS_NAMESPACE", "custom.harness")
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.harness.metrics_namespace == "custom.harness"
    finally:
        get_settings.cache_clear()


def test_harness_env_override_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_HARNESS__HOOK_LOG_LEVEL", "DEBUG")
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.harness.hook_log_level == "DEBUG"
    finally:
        get_settings.cache_clear()
