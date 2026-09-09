"""JSON Schema export for the envelope (documentation and cross-language)."""

from __future__ import annotations

from typing import Any

from mango_contracts.cognitive_signal import CognitiveSignal
from mango_contracts.proposed_action import ProposedAction


def cognitive_signal_json_schema() -> dict[str, Any]:
    """Pydantic JSON Schema for ``CognitiveSignal`` 1.1.0."""
    return CognitiveSignal.model_json_schema()


def proposed_action_json_schema() -> dict[str, Any]:
    """Pydantic JSON Schema for ``ProposedAction``."""
    return ProposedAction.model_json_schema()
