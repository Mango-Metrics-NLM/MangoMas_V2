"""Tests for entry-point discovery of third-party agents."""

from __future__ import annotations

import logging
from collections.abc import Callable
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

    async def handle(
        self,
        request: AgentRequest,  # noqa: ARG002
        ctx: AgentContext,  # noqa: ARG002
    ) -> AgentResponse:
        return AgentResponse(content="plugin", agent=self.name)


def _agent_factory(settings: Any = None) -> _PluginAgent:  # noqa: ARG001
    return _PluginAgent()


def _recording_entry_points(calls: list[str]) -> Callable[..., list[Any]]:
    """Return an ``entry_points`` stub that records the requested group."""

    def _inner(*, group: str) -> list[Any]:
        calls.append(group)
        return []

    return _inner


# ── discover_agents ───────────────────────────────────────────────────────────


def test_discover_agents_registers_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        discovery,
        "entry_points",
        lambda **_: [_FakeEntryPoint(FAKE_PLUGIN_AGENT_NAME, _agent_factory)],
    )
    registry: Registry[Any] = Registry("agent-test")
    registered = discovery.discover_agents(registry, frozenset(), group="x")
    assert registered == [FAKE_PLUGIN_AGENT_NAME]
    assert FAKE_PLUGIN_AGENT_NAME in registry.available()


def test_discover_agents_skips_failing_plugin(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        discovery,
        "entry_points",
        lambda **_: [_FakeEntryPoint("broken", fail=True)],
    )
    registry: Registry[Any] = Registry("agent-test")
    with caplog.at_level(logging.WARNING, logger="mangomas.agents.discovery"):
        registered = discovery.discover_agents(registry, frozenset(), group="x")
    assert registered == []
    assert "broken" not in registry.available()
    assert any(getattr(rec, "event", None) == "agent_plugin_load_failed" for rec in caplog.records)


def test_discover_agents_skips_builtin_collision(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A discovered agent colliding with a built-in name is skipped, not overridden."""
    sentinel = object()
    registry: Registry[Any] = Registry("agent-test")
    registry.register(DEFAULT_AGENT_NAME, sentinel)  # pre-seed the "built-in"
    monkeypatch.setattr(
        discovery,
        "entry_points",
        lambda **_: [_FakeEntryPoint(DEFAULT_AGENT_NAME, _agent_factory)],
    )
    with caplog.at_level(logging.WARNING, logger="mangomas.agents.discovery"):
        registered = discovery.discover_agents(registry, frozenset({DEFAULT_AGENT_NAME}), group="x")
    assert registered == []
    # The built-in binding is untouched — the plugin did not override it.
    assert registry.get(DEFAULT_AGENT_NAME) is sentinel
    assert any(getattr(rec, "event", None) == "agent_plugin_collision" for rec in caplog.records)


# ── ensure_agent_plugins ──────────────────────────────────────────────────────


def test_ensure_agent_plugins_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery, "_discovered_registries", set())
    calls: list[str] = []
    monkeypatch.setattr(discovery, "entry_points", _recording_entry_points(calls))
    registry: Registry[Any] = Registry("agent-test")
    settings = Settings(discovery_enabled=False)
    discovery.ensure_agent_plugins(settings, registry)
    assert calls == []  # discovery never scanned


def test_ensure_agent_plugins_runs_once_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery, "_discovered_registries", set())
    calls: list[str] = []
    monkeypatch.setattr(discovery, "entry_points", _recording_entry_points(calls))
    registry: Registry[Any] = Registry("agent-test")
    settings = Settings(discovery_enabled=True)
    discovery.ensure_agent_plugins(settings, registry)
    discovery.ensure_agent_plugins(settings, registry)  # idempotent — no second scan
    assert calls == [discovery.AGENT_ENTRY_POINT_GROUP]


def test_ensure_agent_plugins_protects_seeded_builtins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Names present before discovery are protected from override."""
    monkeypatch.setattr(discovery, "_discovered_registries", set())
    sentinel = object()
    registry: Registry[Any] = Registry("agent-test")
    registry.register(FAKE_PLUGIN_AGENT_NAME, sentinel)
    monkeypatch.setattr(
        discovery,
        "entry_points",
        lambda **_: [_FakeEntryPoint(FAKE_PLUGIN_AGENT_NAME, _agent_factory)],
    )
    settings = Settings(discovery_enabled=True)
    discovery.ensure_agent_plugins(settings, registry)
    assert registry.get(FAKE_PLUGIN_AGENT_NAME) is sentinel
