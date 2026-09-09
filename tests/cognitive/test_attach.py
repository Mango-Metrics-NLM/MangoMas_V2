"""Direct tests for composition/_attach_cognitive_extras."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from mangomas.cognitive.constants import (
    COGNITIVE_SETTINGS_EXTRAS_KEY,
    COGNITIVE_SINK_EXTRAS_KEY,
)
from mangomas.composition.signal import _attach_cognitive_extras
from mangomas.config import SignalSettings
from mangomas.core.agent import AgentContext


def test_attach_is_noop_when_disabled() -> None:
    extras: dict[str, object] = {"agent_llm_overrides": {}}
    _attach_cognitive_extras(extras, SignalSettings())
    assert extras == {"agent_llm_overrides": {}}


def test_attach_wires_sink_when_enabled(tmp_path: Path) -> None:
    extras: dict[str, object] = {"agent_llm_overrides": {}}
    settings = SignalSettings(enabled=True, dir=str(tmp_path))
    _attach_cognitive_extras(extras, settings)
    assert COGNITIVE_SINK_EXTRAS_KEY in extras
    assert extras[COGNITIVE_SETTINGS_EXTRAS_KEY] is settings


def test_agent_context_has_no_cognitive_field() -> None:
    """Sink stays on extras — core/agent.py must not grow a cognitive_* field."""
    names = {item.name for item in fields(AgentContext)}
    assert names == {
        "llm",
        "repo",
        "extras",
        "tools",
        "memory",
        "embeddings",
        "vector_store",
    }
    assert not any(name.startswith("cognitive") for name in names)
