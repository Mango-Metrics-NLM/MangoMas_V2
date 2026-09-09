"""INV-16 adapter: cognitive fields must never enter a PDP input dict.

The harness PDP lives in the sibling repo. This module is the producer-side
guard a test can fuzz: injecting ``confidence`` / ``allowed_tools`` / ``source``
raises rather than forwarding.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from mango_contracts.validation import POLICY_INPUT_KEYS, policy_input_from_signal

if TYPE_CHECKING:
    from mango_contracts import CognitiveSignal

FORBIDDEN_PDP_KEYS: frozenset[str] = frozenset(
    {
        "confidence",
        "allowed_tools",
        "source",
        "severity",
        "recommendation",
        "payload",
        "producer_id",
        "gate_weights",
        "memory_class",
        "granted_capabilities",
        "execution_approved",
        "gate_passed",
    }
)


class CognitiveControlFieldError(ValueError):
    """Raised when a supposed PDP input carries a cognitive or grant field."""


def refuse_cognitive_pdp_fields(data: Mapping[str, Any]) -> dict[str, str]:
    """Return identity/policy binding only; raise if a control field is present.

    This refuses rather than silently stripping: a stripped field would hide a
    producer bug while still looking like a clean PDP input.
    """
    forbidden = sorted(key for key in data if key in FORBIDDEN_PDP_KEYS)
    if forbidden:
        raise CognitiveControlFieldError(
            f"PDP input must not contain cognitive/control fields: {forbidden}"
        )
    extra = sorted(key for key in data if key not in POLICY_INPUT_KEYS)
    if extra:
        raise CognitiveControlFieldError(
            f"PDP input has unsupported keys: {extra}; only {list(POLICY_INPUT_KEYS)} "
            "are identity/policy binding"
        )
    missing = [key for key in POLICY_INPUT_KEYS if key not in data]
    if missing:
        raise CognitiveControlFieldError(f"PDP input missing identity keys: {missing}")
    return {key: str(data[key]) for key in POLICY_INPUT_KEYS}


def pdp_input_from_signal(signal: CognitiveSignal) -> dict[str, str]:
    """Project a signal to PDP input, then refuse any leaked cognitive key."""
    return refuse_cognitive_pdp_fields(policy_input_from_signal(signal))
