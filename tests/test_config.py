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


# ── LLMSettings — Vertex AI fields ────────────────────────────────────────────


def test_llm_vertex_defaults_do_not_disturb_lmstudio() -> None:
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm.project_id is None
    assert s.llm.location == config_module.DEFAULT_VERTEX_LOCATION
    assert s.llm.credentials_path is None


def test_llm_vertex_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_LLM__PROJECT_ID", "my-gcp-project")
    monkeypatch.setenv("MANGOMAS_LLM__LOCATION", "europe-west4")
    monkeypatch.setenv("MANGOMAS_LLM__CREDENTIALS_PATH", "/keys/sa.json")
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm.project_id == "my-gcp-project"
    assert s.llm.location == "europe-west4"
    assert s.llm.credentials_path == "/keys/sa.json"


# ── DBSettings — Postgres pool fields ─────────────────────────────────────────


def test_db_postgres_pool_defaults() -> None:
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.db.pool_min == config_module.DEFAULT_DB_POOL_MIN
    assert s.db.pool_max == config_module.DEFAULT_DB_POOL_MAX
    assert s.db.connect_timeout_seconds == config_module.DEFAULT_DB_CONNECT_TIMEOUT_SECONDS
    assert s.db.statement_timeout_seconds is config_module.DEFAULT_DB_STATEMENT_TIMEOUT_SECONDS


def test_db_postgres_pool_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_DB__POOL_MIN", "2")
    monkeypatch.setenv("MANGOMAS_DB__POOL_MAX", "20")
    monkeypatch.setenv("MANGOMAS_DB__CONNECT_TIMEOUT_SECONDS", "30.0")
    monkeypatch.setenv("MANGOMAS_DB__STATEMENT_TIMEOUT_SECONDS", "5.5")
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.db.pool_min == 2
    assert s.db.pool_max == 20
    assert s.db.connect_timeout_seconds == 30.0
    assert s.db.statement_timeout_seconds == 5.5


# ── SecretsSettings — GCP fields ──────────────────────────────────────────────


def test_secrets_gcp_defaults() -> None:
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.secrets.provider == config_module.DEFAULT_SECRETS_PROVIDER
    assert s.secrets.project_id is None
    assert s.secrets.timeout_seconds == config_module.DEFAULT_GCP_SECRETS_TIMEOUT_SECONDS
    assert s.secrets.default_version == config_module.DEFAULT_GCP_SECRET_VERSION


def test_secrets_gcp_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_SECRETS__PROVIDER", "gcp")
    monkeypatch.setenv("MANGOMAS_SECRETS__PROJECT_ID", "my-gcp-project")
    monkeypatch.setenv("MANGOMAS_SECRETS__TIMEOUT_SECONDS", "10.0")
    monkeypatch.setenv("MANGOMAS_SECRETS__DEFAULT_VERSION", "3")
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.secrets.provider == "gcp"
    assert s.secrets.project_id == "my-gcp-project"
    assert s.secrets.timeout_seconds == 10.0
    assert s.secrets.default_version == "3"
