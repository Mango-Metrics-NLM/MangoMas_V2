"""Tests for the composition root."""

from __future__ import annotations

from pathlib import Path

from mangomas.composition import (
    _file_memory_factory,
    _HarnessOrchestrator,
    agent_registry,
    build_orchestrator,
)
from mangomas.config import MemorySettings, Settings
from mangomas.core import Orchestrator
from mangomas.core.agent import AgentContext, AgentRequest, Message
from tests.constants import DEFAULT_AGENT_NAME, STUB_REPLY
from tests.fakes import FakeLLM, FakeRepository


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


def test_build_orchestrator_with_memory_enabled_attaches_repo(tmp_path: Path) -> None:
    """The ``memory.enabled=True`` branch wires a :class:`FileMemoryRepository`."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.memory.enabled = True
    settings.memory.memory_dir = str(tmp_path / "mem")
    orch = build_orchestrator(settings)
    try:
        assert orch.context.memory is not None
    finally:
        _close_repo(orch)


def test_file_memory_factory_returns_repository(tmp_path: Path) -> None:
    """Direct factory smoke test — keeps the factory hook covered for refactors."""
    cfg = MemorySettings(
        enabled=True,
        memory_dir=str(tmp_path / "mem"),
    )
    repo = _file_memory_factory(cfg)
    assert repo is not None


async def test_harness_orchestrator_dispatch_wraps_through_to_baseline() -> None:
    """The harness wrapper's dispatch returns the same content as the inner orchestrator."""
    llm = FakeLLM(reply=STUB_REPLY)
    repo = FakeRepository()
    ctx = AgentContext(llm=llm, repo=repo)

    harness_cfg = Settings(_env_file=None).harness  # type: ignore[call-arg]
    harness_cfg.enabled = True
    wrapper = _HarnessOrchestrator(ctx, harness_cfg)

    # Register an agent from the registry so we exercise the real dispatch path.
    factory = agent_registry.get(DEFAULT_AGENT_NAME)
    wrapper.register(factory(None))

    request = AgentRequest(messages=[Message(role="user", content="ping")])
    response = await wrapper.dispatch(DEFAULT_AGENT_NAME, request)

    assert response.content == STUB_REPLY
    # The wrapper persisted the turn via the inner orchestrator.
    assert len(llm.calls) == 1


async def test_harness_orchestrator_stream_dispatch_yields_tokens() -> None:
    """The streaming wrapper still emits tokens through the inner orchestrator."""
    llm = FakeLLM(reply=STUB_REPLY, chunks=["hel", "lo"])
    repo = FakeRepository()
    ctx = AgentContext(llm=llm, repo=repo)

    harness_cfg = Settings(_env_file=None).harness  # type: ignore[call-arg]
    harness_cfg.enabled = True
    wrapper = _HarnessOrchestrator(ctx, harness_cfg)

    factory = agent_registry.get(DEFAULT_AGENT_NAME)
    wrapper.register(factory(None))

    request = AgentRequest(messages=[Message(role="user", content="hi")])
    stream = await wrapper.stream_dispatch(DEFAULT_AGENT_NAME, request)
    received = [chunk async for chunk in stream]

    assert "".join(received)  # at least one non-empty token
