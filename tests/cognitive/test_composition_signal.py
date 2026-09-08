"""Composition wiring for MANGOMAS_SIGNAL__*."""

from __future__ import annotations

from pathlib import Path

from mangomas.cognitive.constants import (
    COGNITIVE_SETTINGS_EXTRAS_KEY,
    COGNITIVE_SINK_EXTRAS_KEY,
)
from mangomas.cognitive.sink import JsonlCognitiveSink
from mangomas.composition import build_orchestrator
from mangomas.config import Settings
from mangomas.core import Orchestrator


def _close_repo(orch: Orchestrator) -> None:
    repo = orch.context.repo
    assert repo is not None
    repo.close()


def test_signal_disabled_does_not_attach_sink() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert COGNITIVE_SINK_EXTRAS_KEY not in orch.context.extras
        assert COGNITIVE_SETTINGS_EXTRAS_KEY not in orch.context.extras
        assert set(orch.context.extras) == {"agent_llm_overrides"}
    finally:
        _close_repo(orch)


def test_signal_enabled_attaches_jsonl_sink(tmp_path: Path) -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.signal.enabled = True
    settings.signal.dir = str(tmp_path)
    orch = build_orchestrator(settings)
    try:
        assert isinstance(orch.context.extras[COGNITIVE_SINK_EXTRAS_KEY], JsonlCognitiveSink)
        assert orch.context.extras[COGNITIVE_SETTINGS_EXTRAS_KEY] is settings.signal
        assert "agent_llm_overrides" in orch.context.extras
    finally:
        _close_repo(orch)
