"""Tests for ``HarnessSettings`` and its integration with the composition root."""

from __future__ import annotations

import pytest

from mangomas.config import (
    DEFAULT_HARNESS_CONFIG_AUDIT_MODE,
    DEFAULT_HARNESS_ENABLED,
    DEFAULT_HARNESS_HOOK_LOG_LEVEL,
    DEFAULT_HARNESS_METRICS_NAMESPACE,
    DEFAULT_HARNESS_STOP_GATE_MODE,
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
    assert s.stop_gate_mode == DEFAULT_HARNESS_STOP_GATE_MODE
    assert s.config_audit_mode == DEFAULT_HARNESS_CONFIG_AUDIT_MODE


def test_harness_stop_gate_and_config_audit_default_to_backwards_compatible_values() -> None:
    """ADR-0011: both new modes must default to today's exact observable behavior."""
    s = HarnessSettings()
    assert s.stop_gate_mode == "advisory"
    assert s.config_audit_mode == "off"


def test_harness_env_override_stop_gate_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_HARNESS__STOP_GATE_MODE", "enforced")
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.harness.stop_gate_mode == "enforced"
    finally:
        get_settings.cache_clear()


def test_harness_env_override_config_audit_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_HARNESS__CONFIG_AUDIT_MODE", "audit")
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.harness.config_audit_mode == "audit"
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
