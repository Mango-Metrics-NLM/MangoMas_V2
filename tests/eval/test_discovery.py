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
        # ADR-0030: overriding a built-in is now opt-in. The behaviour this
        # test pins — that an override announces itself at INFO — is unchanged;
        # only the path to reaching it is explicit.
        discovery.discover_scorers(registry=registry, group="x", allow_builtin_override=True)
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


# ── A plugin must not silently replace a built-in (ADR-0030) ─────────────────

_BUILTIN_SCORER_NAME = "exact_match"


class _HijackedScorer:
    """A plugin double registered under a built-in's name."""

    name = _BUILTIN_SCORER_NAME

    async def score(
        self,
        prediction: str,  # noqa: ARG002
        expected: str,  # noqa: ARG002
        *,
        context: ScorerContext | None = None,  # noqa: ARG002
    ) -> ScoreResult:
        return ScoreResult(score=1.0, passed=True)


def _hijack_factory(options: dict[str, Any]) -> _HijackedScorer:  # noqa: ARG001
    return _HijackedScorer()


def _registry_with_builtin() -> Registry[Any]:
    """A registry already holding a built-in under the contested name."""
    registry: Registry[Any] = Registry("scorer-test")
    registry.register(_BUILTIN_SCORER_NAME, _builtin_factory)
    return registry


def _builtin_factory(options: dict[str, Any]) -> object:  # noqa: ARG001
    return object()


def test_plugin_cannot_override_a_builtin_scorer(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A third-party entry point must not replace a built-in scorer.

    These registries are the inputs to the CI quality gate (exit 3) and the
    regression baseline, so last-call-wins here means an installed package
    decides whether the gate passes. ``agents/discovery.py`` already refuses
    the identical collision; the permissive side was the one guarding the gate.
    """
    registry = _registry_with_builtin()
    monkeypatch.setattr(
        discovery,
        "entry_points",
        lambda *, group: [_FakeEntryPoint(_BUILTIN_SCORER_NAME, _hijack_factory)],  # noqa: ARG005
    )

    with caplog.at_level(logging.WARNING):
        registered = discovery.discover_scorers(registry=registry)

    assert registered == []
    assert registry.get(_BUILTIN_SCORER_NAME) is _builtin_factory
    assert any(getattr(rec, "event", None) == "eval_plugin_collision" for rec in caplog.records)


def test_builtin_override_is_available_behind_the_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented last-call-wins behaviour survives as an explicit opt-in.

    The other direction of the guard (``mango-mutation-proof``): closing the
    default must not delete the capability, or a deployment that legitimately
    ships a replacement scorer has no path forward.
    """
    registry = _registry_with_builtin()
    monkeypatch.setattr(
        discovery,
        "entry_points",
        lambda *, group: [_FakeEntryPoint(_BUILTIN_SCORER_NAME, _hijack_factory)],  # noqa: ARG005
    )

    registered = discovery.discover_scorers(registry=registry, allow_builtin_override=True)

    assert registered == [_BUILTIN_SCORER_NAME]
    assert registry.get(_BUILTIN_SCORER_NAME) is _hijack_factory


def test_a_non_colliding_plugin_still_registers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refusing collisions must not refuse ordinary plugins.

    Without this, ``test_plugin_cannot_override_a_builtin_scorer`` would also
    pass against a discovery function that registered nothing at all.
    """
    registry = _registry_with_builtin()
    monkeypatch.setattr(
        discovery,
        "entry_points",
        lambda *, group: [_FakeEntryPoint(FAKE_PLUGIN_SCORER_NAME, _scorer_factory)],  # noqa: ARG005
    )

    registered = discovery.discover_scorers(registry=registry)

    assert registered == [FAKE_PLUGIN_SCORER_NAME]
    assert registry.get(FAKE_PLUGIN_SCORER_NAME) is _scorer_factory


def test_ensure_eval_plugins_forwards_the_override_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The setting must actually reach ``_discover``, not just exist.

    A flag wired nowhere is the defect this whole audit is about.
    """
    seen: list[bool] = []

    def _record(
        group: str,  # noqa: ARG001 — the recorder cares only about the flag
        registry: Registry[Any],  # noqa: ARG001
        label: str,  # noqa: ARG001
        *,
        allow_builtin_override: bool,
    ) -> list[str]:
        seen.append(allow_builtin_override)
        return []

    monkeypatch.setattr(discovery, "_discover", _record)
    monkeypatch.setattr(discovery, "_discovered_registries", set())
    settings = Settings(discovery_enabled=True, discovery_allow_builtin_override=True)

    discovery.ensure_eval_plugins(settings)

    assert seen == [True, True, True, True]
