"""Tests for entry-point discovery of third-party scorers/sinks."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import pytest

from mangomas import telemetry
from mangomas.config import Settings
from mangomas.eval import discovery
from mangomas.eval.protocol import ScorerContext, ScoreResult
from mangomas.registry import Registry
from tests.constants import FAKE_PLUGIN_SCORER_NAME, FAKE_PLUGIN_SINK_NAME


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


class _PluginScorer:
    """A real Scorer double returned by the fake plugin factory."""

    name = FAKE_PLUGIN_SCORER_NAME

    async def score(
        self,
        prediction: str,  # noqa: ARG002
        expected: str,  # noqa: ARG002
        *,
        context: ScorerContext | None = None,  # noqa: ARG002
    ) -> ScoreResult:
        return ScoreResult(score=1.0, passed=True)


def _scorer_factory(options: dict[str, Any]) -> _PluginScorer:  # noqa: ARG001
    return _PluginScorer()


def _recording_entry_points(calls: list[str]) -> Callable[..., list[Any]]:
    """Return an ``entry_points`` stub that records the requested group."""

    def _inner(*, group: str) -> list[Any]:
        calls.append(group)
        return []

    return _inner


def test_discover_scorers_registers_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        discovery,
        "entry_points",
        lambda **_: [_FakeEntryPoint(FAKE_PLUGIN_SCORER_NAME, _scorer_factory)],
    )
    registry: Registry[Any] = Registry("scorer-test")
    registered = discovery.discover_scorers(registry=registry, group="x")
    assert registered == [FAKE_PLUGIN_SCORER_NAME]
    assert FAKE_PLUGIN_SCORER_NAME in registry.available()


def test_discover_sinks_registers_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        discovery,
        "entry_points",
        lambda **_: [_FakeEntryPoint(FAKE_PLUGIN_SINK_NAME, _scorer_factory)],
    )
    registry: Registry[Any] = Registry("sink-test")
    registered = discovery.discover_sinks(registry=registry, group="x")
    assert registered == [FAKE_PLUGIN_SINK_NAME]


def test_discover_skips_failing_plugin(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        discovery,
        "entry_points",
        lambda **_: [_FakeEntryPoint("broken", fail=True)],
    )
    registry: Registry[Any] = Registry("scorer-test")
    with caplog.at_level(logging.WARNING, logger="mangomas.eval.discovery"):
        registered = discovery.discover_scorers(registry=registry, group="x")
    assert registered == []
    assert "broken" not in registry.available()
    assert any(getattr(rec, "event", None) == "eval_plugin_load_failed" for rec in caplog.records)


def test_discover_scorers_does_not_configure_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D8 regression: discovery must acquire its tracer lazily via the raw
    ``opentelemetry.trace.get_tracer`` (matching ``agents/discovery.py``), not
    the auto-configuring ``mangomas.telemetry.get_tracer``. A module-level
    ``get_tracer(__name__)`` would silently run ``configure_telemetry()`` at
    import time, making the FastAPI lifespan's own call a no-op and locking
    in whatever exporter/log-format happened to be default at import.
    """
    monkeypatch.setattr(telemetry._state, "configured", False)
    monkeypatch.setattr(discovery, "entry_points", lambda **_: [])
    registry: Registry[Any] = Registry("scorer-test")
    discovery.discover_scorers(registry=registry, group="x")
    assert telemetry._state.configured is False


def test_discover_skips_non_callable_factory(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """D8 regression: a plugin whose entry point resolves to a non-callable
    object is skipped with a warning instead of being registered (mirrors
    ``tests.agents.test_discovery.test_discover_agents_skips_non_callable_factory``).
    """
    monkeypatch.setattr(
        discovery,
        "entry_points",
        lambda **_: [_FakeEntryPoint("not-callable", factory=123)],
    )
    registry: Registry[Any] = Registry("scorer-test")
    with caplog.at_level(logging.WARNING, logger="mangomas.eval.discovery"):
        registered = discovery.discover_scorers(registry=registry, group="x")
    assert registered == []
    assert "not-callable" not in registry.available()
    assert any(getattr(rec, "event", None) == "eval_plugin_not_callable" for rec in caplog.records)


def test_discover_override_logs_info(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry: Registry[Any] = Registry("scorer-test")
    registry.register(FAKE_PLUGIN_SCORER_NAME, _scorer_factory)
    monkeypatch.setattr(
        discovery,
        "entry_points",
        lambda **_: [_FakeEntryPoint(FAKE_PLUGIN_SCORER_NAME, _scorer_factory)],
    )
    with caplog.at_level(logging.INFO, logger="mangomas.eval.discovery"):
        discovery.discover_scorers(registry=registry, group="x")
    assert any(getattr(rec, "event", None) == "eval_plugin_override" for rec in caplog.records)


def test_ensure_eval_plugins_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery, "_discovered_registries", set())
    calls: list[str] = []
    monkeypatch.setattr(discovery, "entry_points", _recording_entry_points(calls))
    discovery.ensure_eval_plugins(Settings(discovery_enabled=False))
    assert calls == []


def test_ensure_eval_plugins_runs_once_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery, "_discovered_registries", set())
    calls: list[str] = []
    monkeypatch.setattr(discovery, "entry_points", _recording_entry_points(calls))
    settings = Settings(discovery_enabled=True)
    discovery.ensure_eval_plugins(settings)
    discovery.ensure_eval_plugins(settings)  # idempotent — no second scan
    assert calls == [
        discovery.SCORER_ENTRY_POINT_GROUP,
        discovery.SINK_ENTRY_POINT_GROUP,
        discovery.TARGET_ENTRY_POINT_GROUP,
        discovery.DATASET_SOURCE_ENTRY_POINT_GROUP,
    ]


def test_ensure_eval_plugins_rescans_when_a_registry_is_swapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registry not yet in the latch is scanned even if the others are.

    Regression test for the previous single global ``bool`` latch, which
    would have skipped this scan entirely once *any* call had succeeded.
    """
    monkeypatch.setattr(discovery, "_discovered_registries", set())
    calls: list[str] = []
    monkeypatch.setattr(discovery, "entry_points", _recording_entry_points(calls))
    settings = Settings(discovery_enabled=True)
    discovery.ensure_eval_plugins(settings)
    calls.clear()

    fresh_scorer_registry: Registry[Any] = Registry("scorer-fresh")
    monkeypatch.setattr(discovery, "scorer_registry", fresh_scorer_registry)
    discovery.ensure_eval_plugins(settings)
    assert calls == [
        discovery.SCORER_ENTRY_POINT_GROUP,
        discovery.SINK_ENTRY_POINT_GROUP,
        discovery.TARGET_ENTRY_POINT_GROUP,
        discovery.DATASET_SOURCE_ENTRY_POINT_GROUP,
    ]
