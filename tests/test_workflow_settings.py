"""Tests for WorkflowSettings (default-OFF, env-driven, validated)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mangomas.config import Settings, WorkflowSettings, get_settings


def test_defaults_are_disabled() -> None:
    cfg = WorkflowSettings()
    assert cfg.enabled is False
    assert cfg.definition is None


def test_enabled_requires_definition() -> None:
    with pytest.raises(ValidationError, match="definition"):
        WorkflowSettings(enabled=True)


def test_enabled_with_definition_ok() -> None:
    cfg = WorkflowSettings(enabled=True, definition="./graph.json")
    assert cfg.enabled is True
    assert cfg.definition == "./graph.json"


def test_top_level_settings_default_workflow_off() -> None:
    """Backwards-compat: Settings() still constructs with workflow OFF."""
    cfg = Settings()
    assert cfg.workflow.enabled is False


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_WORKFLOW__ENABLED", "true")
    monkeypatch.setenv("MANGOMAS_WORKFLOW__DEFINITION", "./g.json")
    get_settings.cache_clear()
    try:
        cfg = Settings()
        assert cfg.workflow.enabled is True
        assert cfg.workflow.definition == "./g.json"
    finally:
        get_settings.cache_clear()
