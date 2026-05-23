"""Tests for the composition root."""

from __future__ import annotations

from mangomas.composition import _HarnessOrchestrator, agent_registry, build_orchestrator
from mangomas.config import Settings
from mangomas.core import Orchestrator


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


def test_build_orchestrator_disabled_harness_returns_plain_orchestrator() -> None:
    """With ``harness.enabled=False`` (default), no subclass is engaged."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert type(orch) is Orchestrator
        assert not isinstance(orch, _HarnessOrchestrator)
    finally:
        _close_repo(orch)


def test_build_orchestrator_enabled_harness_returns_wrapper() -> None:
    """With ``harness.enabled=True``, the harness subclass is returned and agents match."""
    baseline_settings = Settings(_env_file=None)  # type: ignore[call-arg]
    baseline_settings.db.url = "sqlite:///:memory:"
    baseline = build_orchestrator(baseline_settings)
    baseline_agents = baseline.list_agents()
    _close_repo(baseline)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.harness.enabled = True
    orch = build_orchestrator(settings)
    try:
        assert isinstance(orch, _HarnessOrchestrator)
        assert orch.list_agents() == baseline_agents
    finally:
        _close_repo(orch)


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
