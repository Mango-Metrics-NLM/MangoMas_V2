"""Tests for pydantic-settings configuration."""

from __future__ import annotations

import importlib

import pytest

from mangomas import config as config_module


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    config_module.get_settings.cache_clear()


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MANGOMAS_LLM__BASE_URL", raising=False)
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm.base_url.endswith("/v1")
    assert s.llm.model == "local-model"
    assert s.db.url.startswith("sqlite:///")
    assert s.api.port == 8000


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_LLM__BASE_URL", "http://example.test/v1")
    monkeypatch.setenv("MANGOMAS_LLM__MODEL", "custom")
    monkeypatch.setenv("MANGOMAS_API__PORT", "9001")
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm.base_url == "http://example.test/v1"
    assert s.llm.model == "custom"
    assert s.api.port == 9001


def test_get_settings_is_cached() -> None:
    a = config_module.get_settings()
    b = config_module.get_settings()
    assert a is b


def test_module_reimport_safe() -> None:
    importlib.reload(config_module)
    assert config_module.get_settings().env in {"local", "dev", "prod"}


# ── LoopSettings ──────────────────────────────────────────────────────────────


def test_loop_settings_defaults() -> None:
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.loop.max_steps == config_module.DEFAULT_LOOP_MAX_STEPS
    assert s.loop.step_timeout_seconds == config_module.DEFAULT_LOOP_STEP_TIMEOUT


def test_loop_settings_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_LOOP__MAX_STEPS", "10")
    monkeypatch.setenv("MANGOMAS_LOOP__STEP_TIMEOUT_SECONDS", "5.0")
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.loop.max_steps == 10
    assert s.loop.step_timeout_seconds == 5.0


# ── MemorySettings ────────────────────────────────────────────────────────────


def test_memory_settings_defaults() -> None:
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.memory.enabled is False
    assert s.memory.provider == config_module.DEFAULT_MEMORY_PROVIDER
    assert s.memory.memory_dir == config_module.DEFAULT_MEMORY_DIR
    assert s.memory.index_file == config_module.DEFAULT_MEMORY_INDEX


def test_memory_settings_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_MEMORY__ENABLED", "true")
    monkeypatch.setenv("MANGOMAS_MEMORY__MEMORY_DIR", "custom_mem")
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.memory.enabled is True
    assert s.memory.memory_dir == "custom_mem"
