"""Tests for entry-point discovery of third-party agents."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from mangomas.agents import discovery
from mangomas.config import Settings
from mangomas.core.agent import AgentContext, AgentRequest, AgentResponse
from mangomas.registry import Registry
from tests.constants import DEFAULT_AGENT_NAME, FAKE_PLUGIN_AGENT_NAME


class _FakeEntryPoint:
    """Minimal stand-in for ``importlib.metadata.EntryPoint``."""

    def __init__(self, name: str, factory: Any = None, *, fail: bool = False) -> None:
        self.name = name
        self._factory = factory
        self._fail = fail

    def load(self) -> Any:
        if self._fail:
            raise ImportError("plugin import failed")
        return self._factory


class _PluginAgent:
    """A real Agent double returned by the fake plugin factory."""

    name = FAKE_PLUGIN_AGENT_NAME

    async def handle(self, request: AgentRequest, _ctx: AgentContext) -> AgentResponse:
        return AgentResponse(content=f"got:{len(request.messages)}", agent=self.name)


def _agent_factory(settings: Any) -> _PluginAgent:  # noqa: ARG001 — mirrors AgentFactory shape
    return _PluginAgent()


@pytest.fixture(autouse=True)
def _reset_discovery_latch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the once-per-process latch so tests are order-independent."""
    monkeypatch.setattr(discovery, "_discovered", False)


def test_discover_agents_registers_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        discovery,
        "entry_points",
        lambda **_: [_FakeEntryPoint(FAKE_PLUGIN_AGENT_NAME, _agent_factory)],
    )
    registry: Registry[Any] = Registry("agent-test")
    registered = discovery.discover_agents(registry=registry, group="x")
    assert registered == [FAKE_PLUGIN_AGENT_NAME]
    assert FAKE_PLUGIN_AGENT_NAME in registry.available()
    # The registered value is the factory, which constructs the agent double.
    assert registry.get(FAKE_PLUGIN_AGENT_NAME)(None).name == FAKE_PLUGIN_AGENT_NAME


def test_discover_skips_failing_plugin(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        discovery,
        "entry_points",
        lambda **_: [
            _FakeEntryPoint("broken", fail=True),
            _FakeEntryPoint(FAKE_PLUGIN_AGENT_NAME, _agent_factory),
        ],
    )
    registry: Registry[Any] = Registry("agent-test")
    with caplog.at_level(logging.WARNING, logger="mangomas.agents.discovery"):
        registered = discovery.discover_agents(registry=registry, group="x")
    # The failing plugin is skipped; the healthy one still registers.
    assert registered == [FAKE_PLUGIN_AGENT_NAME]
    assert "broken" not in registry.available()
    assert any(getattr(rec, "event", None) == "agent_plugin_load_failed" for rec in caplog.records)


def test_discover_skips_non_callable_plugin(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        discovery,
        "entry_points",
        lambda **_: [_FakeEntryPoint("not_callable", factory="i am a string")],
    )
    registry: Registry[Any] = Registry("agent-test")
    with caplog.at_level(logging.WARNING, logger="mangomas.agents.discovery"):
        registered = discovery.discover_agents(registry=registry, group="x")
    assert registered == []
    assert "not_callable" not in registry.available()
    assert any(getattr(rec, "event", None) == "agent_plugin_load_failed" for rec in caplog.records)


def test_discover_override_logs_info(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry: Registry[Any] = Registry("agent-test")
    registry.register(DEFAULT_AGENT_NAME, _agent_factory)
    monkeypatch.setattr(
        discovery,
        "entry_points",
        lambda **_: [_FakeEntryPoint(DEFAULT_AGENT_NAME, _agent_factory)],
    )
    with caplog.at_level(logging.INFO, logger="mangomas.agents.discovery"):
        discovery.discover_agents(registry=registry, group="x")
    assert any(getattr(rec, "event", None) == "agent_plugin_override" for rec in caplog.records)


def test_ensure_agent_plugins_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _record(*, group: str) -> list[Any]:
        calls.append(group)
        return []

    monkeypatch.setattr(discovery, "entry_points", _record)
    registry: Registry[Any] = Registry("agent-test")
    discovery.ensure_agent_plugins(Settings(discovery_enabled=False), registry)
    assert calls == []


def test_ensure_agent_plugins_runs_once_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _record(*, group: str) -> list[Any]:
        calls.append(group)
        return []

    monkeypatch.setattr(discovery, "entry_points", _record)
    registry: Registry[Any] = Registry("agent-test")
    settings = Settings(discovery_enabled=True)
    discovery.ensure_agent_plugins(settings, registry)
    discovery.ensure_agent_plugins(settings, registry)  # idempotent — no second scan
    assert calls == [discovery.AGENT_ENTRY_POINT_GROUP]
