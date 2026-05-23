"""Tests for the composition root."""

from __future__ import annotations

from typing import Any

import pytest

from mangomas.composition import (
    _storage_registry,
    agent_registry,
    build_orchestrator,
    llm_registry,
)
from mangomas.config import (
    DBSettings,
    LLMSettings,
    SecretsSettings,
    Settings,
)
from mangomas.core import Orchestrator
from mangomas.errors import ConfigError
from mangomas.secrets import secrets_registry


def _close_repo(orch: Orchestrator) -> None:
    repo = orch.context.repo
    assert repo is not None
    repo.close()


def test_build_orchestrator_wires_chat_agent() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert "chat" in orch.list_agents()
        assert orch.context.llm is not None
        assert orch.context.repo is not None
    finally:
        _close_repo(orch)


def test_build_orchestrator_wires_summarize_agent() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert "summarize" in orch.list_agents()
    finally:
        _close_repo(orch)


def test_default_agents_are_registered_in_agent_registry() -> None:
    assert "chat" in agent_registry.available()
    assert "summarize" in agent_registry.available()
    assert "tool" in agent_registry.available()
    assert "planner" in agent_registry.available()
    assert "reviewer" in agent_registry.available()


def test_build_orchestrator_wires_all_agents() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.memory.enabled = False
    orch = build_orchestrator(settings)
    try:
        agents = orch.list_agents()
        assert "chat" in agents
        assert "summarize" in agents
        assert "tool" in agents
        assert "planner" in agents
        assert "reviewer" in agents
        assert orch.context.memory is None
    finally:
        _close_repo(orch)


# ── Cloud provider registration (v0.3.0) ──────────────────────────────────────


def test_vertex_factory_registered_in_llm_registry() -> None:
    assert "vertex" in llm_registry.available()


def test_postgres_factory_registered_in_storage_registry() -> None:
    assert "postgres" in _storage_registry.available()


def test_vertex_factory_raises_config_error_when_project_missing() -> None:
    """Calling the vertex factory without LLMSettings.project must fail loud."""
    factory = llm_registry.get("vertex")
    cfg = LLMSettings(provider="vertex", project=None)
    with pytest.raises(ConfigError, match="MANGOMAS_LLM__PROJECT"):
        factory(cfg)


def test_postgres_factory_constructs_repo_without_io() -> None:
    """The postgres factory must not open a pool — preserves the sync shape."""
    factory = _storage_registry.get("postgres")
    cfg = DBSettings(provider="postgres", url="postgresql://h/db")
    repo = factory(cfg)
    # Lazy-pool invariant from PostgresRepository.
    assert repo._pool is None


def test_gcp_secrets_lazy_registers_on_build_when_provider_selected() -> None:
    """When secrets.provider='gcp', build_orchestrator must lazy-register it."""
    settings = Settings(
        llm=LLMSettings(provider="lmstudio", api_key="inline"),
        db=DBSettings(provider="sqlite", url="sqlite:///:memory:"),
        secrets=SecretsSettings(provider="gcp", project_id="test-proj"),
    )
    # Drop any prior gcp binding so we can observe the lazy registration.
    if "gcp" in secrets_registry.available():
        secrets_registry._store.pop("gcp", None)

    captured: dict[str, Any] = {}

    def _capturing_llm_factory(cfg: LLMSettings) -> object:
        captured["api_key"] = cfg.api_key

        class _Stub:
            async def aclose(self) -> None: ...

        return _Stub()

    with llm_registry.scoped("lmstudio", _capturing_llm_factory):
        orch = build_orchestrator(settings)
        try:
            assert "gcp" in secrets_registry.available()
        finally:
            _close_repo(orch)
            # Tidy up so other tests don't see the lazily-registered provider.
            secrets_registry._store.pop("gcp", None)


def test_gcp_secrets_lazy_register_raises_when_project_id_missing() -> None:
    """build_orchestrator must surface the ConfigError eagerly when misconfigured."""
    settings = Settings(
        llm=LLMSettings(provider="lmstudio", api_key="inline"),
        db=DBSettings(provider="sqlite", url="sqlite:///:memory:"),
        secrets=SecretsSettings(provider="gcp", project_id=None),
    )
    if "gcp" in secrets_registry.available():
        secrets_registry._store.pop("gcp", None)
    with pytest.raises(ConfigError, match="MANGOMAS_SECRETS__PROJECT_ID"):
        build_orchestrator(settings)
