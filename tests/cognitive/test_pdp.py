"""INV-16: producer-side PDP adapter refuses cognitive fields."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from tests.constants import (
    DEFAULT_SIGNAL_POLICY_ID,
    DEFAULT_SIGNAL_POLICY_SNAPSHOT_HASH,
    DEFAULT_SIGNAL_POLICY_VERSION,
)
from tests.mango_contracts.constants import RUN_ID, TASK_ID, envelope_base

from mango_contracts import CognitiveSignal
from mango_contracts.validation import POLICY_INPUT_KEYS
from mangomas.cognitive.pdp import (
    FORBIDDEN_PDP_KEYS,
    CognitiveControlFieldError,
    pdp_input_from_signal,
    refuse_cognitive_pdp_fields,
)

_IDENTITY = {
    "run_id": str(RUN_ID),
    "task_id": str(TASK_ID),
    "policy_id": DEFAULT_SIGNAL_POLICY_ID,
    "policy_version": DEFAULT_SIGNAL_POLICY_VERSION,
    "policy_snapshot_hash": DEFAULT_SIGNAL_POLICY_SNAPSHOT_HASH,
}


def test_clean_identity_is_forwarded() -> None:
    assert refuse_cognitive_pdp_fields(_IDENTITY) == _IDENTITY


def test_refuse_does_not_strip_and_forward() -> None:
    dirty = {**_IDENTITY, "confidence": 0.99}
    with pytest.raises(CognitiveControlFieldError, match="confidence"):
        refuse_cognitive_pdp_fields(dirty)


@given(st.sampled_from(sorted(FORBIDDEN_PDP_KEYS)))
def test_injected_control_field_is_refused(key: str) -> None:
    dirty = {**_IDENTITY, key: "mangomas" if key != "confidence" else 0.9}
    with pytest.raises(CognitiveControlFieldError):
        refuse_cognitive_pdp_fields(dirty)


def test_unknown_extra_key_is_refused() -> None:
    with pytest.raises(CognitiveControlFieldError, match="unsupported"):
        refuse_cognitive_pdp_fields({**_IDENTITY, "retry_limit": "1"})


def test_missing_identity_key_is_refused() -> None:
    incomplete = {k: v for k, v in _IDENTITY.items() if k != "run_id"}
    with pytest.raises(CognitiveControlFieldError, match="missing"):
        refuse_cognitive_pdp_fields(incomplete)


@pytest.mark.parametrize("low,high", [(0.0, 1.0), (0.01, 0.99)])
def test_confidence_cannot_change_pdp_projection(low: float, high: float) -> None:
    low_signal = CognitiveSignal.model_validate(envelope_base(confidence=low))
    high_signal = CognitiveSignal.model_validate(envelope_base(confidence=high))
    left = pdp_input_from_signal(low_signal)
    right = pdp_input_from_signal(high_signal)
    assert left == right
    assert set(left) == set(POLICY_INPUT_KEYS)
    assert "confidence" not in left
