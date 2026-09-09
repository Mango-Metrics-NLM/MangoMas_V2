"""Valid CognitiveSignal envelopes parse and round-trip."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from tests.mango_contracts.constants import (
    POLICY_ID,
    POLICY_SNAPSHOT_HASH,
    POLICY_VERSION,
    PRODUCER_VERSION,
    RUN_ID,
    TASK_ID,
)

from mango_contracts import (
    SCHEMA_VERSION,
    CognitiveRecommendation,
    CognitiveSignal,
    EvidenceBundle,
    EvidenceStatus,
    RecommendationDisposition,
    SignalKind,
    cognitive_signal_json_schema,
    ingest_cognitive_signal,
    proposed_action_json_schema,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> dict[str, Any]:
    loaded = json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_review_fixture_validates() -> None:
    signal = CognitiveSignal.model_validate(_load("valid_review_finding.json"))
    assert signal.schema_version == SCHEMA_VERSION
    assert signal.signal_kind is SignalKind.REVIEW_FINDING
    assert signal.recommendation is not None
    assert signal.evidence.status is EvidenceStatus.SUFFICIENT
    assert signal.is_prompt_eligible(datetime(2026, 9, 8, 22, tzinfo=UTC)) is False


def test_routing_fixture_is_prompt_eligible_before_expiry() -> None:
    signal = CognitiveSignal.model_validate(_load("valid_routing_recommendation.json"))
    now = datetime(2026, 9, 8, 21, 10, tzinfo=UTC)
    assert signal.is_prompt_eligible(now) is True
    assert signal.is_expired(now) is False


def test_create_stamps_ttl_and_kind() -> None:
    created = datetime(2026, 9, 8, 12, tzinfo=UTC)
    signal = CognitiveSignal.create(
        run_id=RUN_ID,
        task_id=TASK_ID,
        producer_id="mangomas.planner.v2",
        producer_version=PRODUCER_VERSION,
        signal_kind=SignalKind.PLANNING_PROPOSAL,
        summary="Break the work into verified steps.",
        policy_id=POLICY_ID,
        policy_version=POLICY_VERSION,
        policy_snapshot_hash=POLICY_SNAPSHOT_HASH,
        payload={"goal": "ship contracts", "steps": ["write schema", "write tests"]},
        created_at=created,
        signal_id=uuid4(),
        recommendation=CognitiveRecommendation(
            disposition=RecommendationDisposition.OBSERVE,
            summary="Keep planning advisory.",
            rationale="Plans do not execute.",
        ),
        evidence=EvidenceBundle(status=EvidenceStatus.NONE),
        tags=[" Planning ", "planning"],
        uncertainty_notes=["Scope may grow."],
        confidence=0.5,
    )
    assert signal.signal_type == "planning.proposal"
    assert signal.expires_at == created + timedelta(seconds=signal.ttl_seconds)
    assert signal.tags == ["planning"]


def test_json_schema_exports_forbid_extra() -> None:
    schema = cognitive_signal_json_schema()
    assert schema.get("additionalProperties") is False
    action_schema = proposed_action_json_schema()
    assert "proposal_id" in action_schema["properties"]


def test_duplicate_and_empty_tags_are_dropped() -> None:
    raw = _load("valid_review_finding.json")
    raw["tags"] = ["Alpha", "", "alpha", "beta"]
    signal = CognitiveSignal.model_validate(raw)
    assert signal.tags == ["alpha", "beta"]


def test_z_suffix_timestamps_are_accepted() -> None:
    raw = _load("valid_review_finding.json")
    raw["created_at"] = "2026-09-08T21:00:00Z"
    raw["expires_at"] = "2026-09-09T21:00:00Z"
    signal = CognitiveSignal.model_validate(raw)
    assert signal.created_at.tzinfo is not None


def test_create_defaults_stamp_fresh_ids() -> None:
    signal = CognitiveSignal.create(
        run_id=RUN_ID,
        task_id=TASK_ID,
        producer_id="mangomas.chat.v2",
        producer_version=PRODUCER_VERSION,
        signal_kind=SignalKind.CONTEXT_SUMMARY,
        summary="Session context only.",
        policy_id=POLICY_ID,
        policy_version=POLICY_VERSION,
        policy_snapshot_hash=POLICY_SNAPSHOT_HASH,
    )
    assert signal.signal_id
    assert signal.is_expired(signal.expires_at) is True
    assert signal.is_prompt_eligible() is False


def test_routing_ingest_can_be_prompt_eligible() -> None:
    raw = _load("valid_routing_recommendation.json")
    now = datetime(2026, 9, 8, 21, 10, tzinfo=UTC)
    result = ingest_cognitive_signal(raw, now=now)
    assert result.accepted is True
    assert result.prompt_eligible is True
    assert result.archive_only is False


def test_expired_routing_is_not_prompt_eligible() -> None:
    signal = CognitiveSignal.model_validate(_load("valid_routing_recommendation.json"))
    later = datetime(2026, 9, 10, tzinfo=UTC)
    assert signal.is_expired(later) is True
    assert signal.is_prompt_eligible(later) is False


def test_pii_payload_round_trip() -> None:
    raw = _load("valid_pii_finding.json")
    signal = CognitiveSignal.model_validate(raw)
    assert signal.signal_kind is SignalKind.PII_FINDING
    result = ingest_cognitive_signal(raw, now=datetime(2026, 9, 8, 21, 10, tzinfo=UTC))
    assert result.accepted is True
    assert result.prompt_eligible is False
