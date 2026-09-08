"""INV-16: cognitive fields cannot change a policy disposition."""

from __future__ import annotations

from copy import deepcopy

import pytest
from tests.mango_contracts.constants import envelope_base

from mango_contracts import CognitiveSignal
from mango_contracts.validation import POLICY_INPUT_KEYS, policy_input_from_signal


def _disposition(policy_input: dict[str, str]) -> str:
    """Stand-in PDP: identity/policy binding only, no cognitive fields."""
    assert set(policy_input) == set(POLICY_INPUT_KEYS)
    return "requires_independent_gate_evidence"


@pytest.mark.parametrize("low,high", [(0.0, 1.0), (0.01, 0.99), (0.40, 0.41)])
def test_confidence_cannot_change_harness_disposition(low: float, high: float) -> None:
    low_signal = CognitiveSignal.model_validate(envelope_base(confidence=low))
    high_signal = CognitiveSignal.model_validate(envelope_base(confidence=high))
    assert _disposition(policy_input_from_signal(low_signal)) == _disposition(
        policy_input_from_signal(high_signal)
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("severity", "critical"),
        ("summary", "A completely different summary."),
        ("uncertainty_notes", ["totally unsure"]),
        ("tags", ["unrelated"]),
        ("memory_class", "archive_only"),
        ("producer_id", "mangomas.chat.v2"),
        (
            "payload",
            {
                "finding_id": "finding_other_001",
                "affected_artifacts": ["workspace://root/other.py"],
                "impact_statement": "Different impact.",
                "reproduction_notes": ["other"],
            },
        ),
    ],
)
def test_cognitive_field_cannot_change_policy_input_shape(field: str, value: object) -> None:
    baseline = CognitiveSignal.model_validate(envelope_base())
    variant_raw = envelope_base()
    variant_raw[field] = value
    variant = CognitiveSignal.model_validate(variant_raw)
    left = policy_input_from_signal(baseline)
    right = policy_input_from_signal(variant)
    assert set(left) == set(POLICY_INPUT_KEYS)
    assert set(right) == set(POLICY_INPUT_KEYS)
    for key in ("confidence", "severity", "summary", "payload", "recommendation"):
        assert key not in left
        assert key not in right
    assert _disposition(left) == _disposition(right)


def test_recommendation_cannot_enter_policy_input() -> None:
    raw = envelope_base()
    raw["recommendation"] = {
        "disposition": "recommend_stop",
        "summary": "Stop the workflow.",
        "rationale": "Still only a recommendation.",
        "proposed_action_refs": [],
        "requested_review_roles": ["security-reviewer"],
    }
    signal = CognitiveSignal.model_validate(raw)
    policy_input = policy_input_from_signal(signal)
    dumped = deepcopy(policy_input)
    assert "recommend_stop" not in dumped.values()
    assert _disposition(policy_input) == "requires_independent_gate_evidence"


def test_policy_input_is_stable_across_identical_identity_fields() -> None:
    first = policy_input_from_signal(CognitiveSignal.model_validate(envelope_base()))
    second = policy_input_from_signal(CognitiveSignal.model_validate(envelope_base()))
    assert first == second


def test_signal_type_cannot_change_policy_input() -> None:
    """Routing vs review is cognitive content; the PDP projection stays identity-only."""
    review = CognitiveSignal.model_validate(envelope_base())
    routing_raw = envelope_base(
        signal_type="routing.recommendation",
        signal_kind="routing.recommendation",
        memory_class="prompt_injectable",
        severity="info",
        payload={
            "recommended_cognitive_role": "defect_analysis",
            "rationale": "Looks like a regression.",
            "alternatives_considered": [],
        },
    )
    routing_raw["evidence"] = {"status": "partial", "refs": []}
    routing = CognitiveSignal.model_validate(routing_raw)
    left = policy_input_from_signal(review)
    right = policy_input_from_signal(routing)
    assert left == right
    assert _disposition(left) == _disposition(right)
