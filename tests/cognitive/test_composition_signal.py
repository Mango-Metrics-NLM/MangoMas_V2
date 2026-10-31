"""Composition wiring for MANGOMAS_SIGNAL__*."""

from __future__ import annotations

from pathlib import Path

from tests.constants import IN_MEMORY_SQLITE_URL

from mangomas.agents._prompt import (
    AGENT_LLM_OVERRIDES_EXTRAS_KEY,
    LLM_PROFILE_INFO_EXTRAS_KEY,
    LLM_PROFILES_EXTRAS_KEY,
)
from mangomas.cognitive.constants import (
    COGNITIVE_SETTINGS_EXTRAS_KEY,
    COGNITIVE_SINK_EXTRAS_KEY,
)
from mangomas.cognitive.sink import JsonlCognitiveSink
from mangomas.composition import build_orchestrator
from mangomas.composition.agents import STRUCTURED_AGENT_FIELDS_EXTRAS_KEY
from mangomas.config import Settings
from mangomas.core import Orchestrator


def _close_repo(orch: Orchestrator) -> None:
    repo = orch.context.repo
    assert repo is not None
    repo.close()


def test_signal_disabled_does_not_attach_sink() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = IN_MEMORY_SQLITE_URL
    orch = build_orchestrator(settings)
    try:
        assert COGNITIVE_SINK_EXTRAS_KEY not in orch.context.extras
        assert COGNITIVE_SETTINGS_EXTRAS_KEY not in orch.context.extras
        # Exact set, deliberately: this is what catches a stray extras key
        # leaking out of composition. Every member is a named constant so the
        # assertion cannot drift from the writer's spelling. The two profile
        # keys (spec-0040) are always published, empty when no profile is
        # configured — the same shape as the override map.
        assert set(orch.context.extras) == {
            AGENT_LLM_OVERRIDES_EXTRAS_KEY,
            LLM_PROFILES_EXTRAS_KEY,
            LLM_PROFILE_INFO_EXTRAS_KEY,
            STRUCTURED_AGENT_FIELDS_EXTRAS_KEY,
        }
    finally:
        _close_repo(orch)


def test_signal_enabled_attaches_jsonl_sink(tmp_path: Path) -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = IN_MEMORY_SQLITE_URL
    settings.signal.enabled = True
    settings.signal.dir = str(tmp_path)
    orch = build_orchestrator(settings)
    try:
        assert isinstance(orch.context.extras[COGNITIVE_SINK_EXTRAS_KEY], JsonlCognitiveSink)
        assert orch.context.extras[COGNITIVE_SETTINGS_EXTRAS_KEY] is settings.signal
        assert "agent_llm_overrides" in orch.context.extras
    finally:
        _close_repo(orch)
