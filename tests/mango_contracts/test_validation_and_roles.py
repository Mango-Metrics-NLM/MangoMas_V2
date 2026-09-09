"""Ingestion, payload registry, roles, and ProposedAction helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from tests.mango_contracts.constants import (
    POLICY_VERSION,
    RUN_ID,
    TASK_ID,
    envelope_base,
)

from mango_contracts import (
    ProposedAction,
    ProposedActionKind,
    SignalKind,
    UnknownProducerError,
    UnknownReviewRoleError,
    compute_idempotency_key,
    create_review_work_item,
    ingest_cognitive_signal,
    map_requested_review_roles,
    producer_id_for_agent,
    validate_signal_payload,
)
from mango_contracts.cognitive_signal import CognitiveSignal
from mango_contracts.payloads import FORBIDDEN_RECOMMENDED_ROLES, ReviewFindingPayload
from mango_contracts.proposed_action import SHELL_INTENT_MARKERS
from mango_contracts.roles import HARNESS_REVIEW_ROLES


def test_ingest_accepts_valid_review() -> None:
    result = ingest_cognitive_signal(envelope_base())
    assert result.accepted is True
    assert result.prompt_eligible is False
    assert result.archive_only is True
    assert result.signal is not None


def test_ingest_rejects_schema_errors() -> None:
    raw = envelope_base(allowed_tools=["shell"])
    result = ingest_cognitive_signal(raw)
    assert result.accepted is False
    assert result.signal is None
    assert result.reason.startswith("schema_rejected:")


def test_ingest_archives_expired_signals() -> None:
    raw = envelope_base()
    created = datetime(2020, 1, 1, tzinfo=UTC)
    raw["created_at"] = created.isoformat()
    raw["expires_at"] = (created + timedelta(seconds=raw["ttl_seconds"])).isoformat()
    result = ingest_cognitive_signal(raw)
    assert result.accepted is True
    assert result.archive_only is True
    assert result.reason == "expired_signal_archived"


def test_ingest_archives_types_without_payload_schema() -> None:
    raw = envelope_base(
        signal_type="context.summary",
        signal_kind="context.summary",
        payload={},
    )
    result = ingest_cognitive_signal(raw)
    assert result.accepted is True
    assert result.archive_only is True
    assert result.reason == "unknown_payload_schema_archived"


def test_ingest_rejects_payload_schema_mismatch() -> None:
    raw = envelope_base()
    raw["payload"] = {"finding_id": "short"}
    result = ingest_cognitive_signal(raw)
    assert result.accepted is False


def test_validate_signal_payload_returns_model() -> None:
    signal = CognitiveSignal.model_validate(envelope_base())
    parsed = validate_signal_payload(signal)
    assert isinstance(parsed, ReviewFindingPayload)


def test_validate_signal_payload_unknown_returns_none() -> None:
    raw = envelope_base(
        signal_type="quality.finding",
        signal_kind="quality.finding",
        payload={},
    )
    signal = CognitiveSignal.model_validate(raw)
    assert validate_signal_payload(signal) is None


def test_create_review_work_item_requires_recommendation() -> None:
    signal = CognitiveSignal.model_validate(envelope_base())
    with pytest.raises(ValueError, match="recommendation"):
        create_review_work_item(signal)


def test_create_review_work_item_is_not_an_approval() -> None:
    raw = envelope_base()
    raw["recommendation"] = {
        "disposition": "request_review",
        "summary": "Please review.",
        "rationale": "Finding needs a human.",
        "proposed_action_refs": [],
        "requested_review_roles": ["peer-reviewer"],
    }
    item = create_review_work_item(CognitiveSignal.model_validate(raw))
    assert item["requires_harness_authorization"] is True
    assert item["requested_disposition"] == "request_review"


def test_producer_id_for_known_agents() -> None:
    assert producer_id_for_agent("planner") == "mangomas.planner.v2"
    assert producer_id_for_agent("reviewer") == "mangomas.reviewer.v2"


def test_unknown_agent_does_not_default() -> None:
    with pytest.raises(UnknownProducerError, match="quality agent"):
        producer_id_for_agent("quality agent")


def test_unknown_review_role_does_not_default_to_implementer() -> None:
    assert "implementer" not in HARNESS_REVIEW_ROLES
    with pytest.raises(UnknownReviewRoleError, match="implementer"):
        map_requested_review_roles(["implementer"])
    assert map_requested_review_roles(["security-reviewer"]) == ["security-reviewer"]


def test_proposed_action_idempotency_is_stable() -> None:
    kind = ProposedActionKind.READ_FILE
    intent = "Read the contracts package README"
    key = compute_idempotency_key(
        RUN_ID,
        TASK_ID,
        kind=kind,
        intent=intent,
        artifact_refs=["workspace://root/README.md"],
        policy_version=POLICY_VERSION,
    )
    again = compute_idempotency_key(
        RUN_ID,
        TASK_ID,
        kind=kind,
        intent=intent,
        artifact_refs=["workspace://root/README.md"],
        policy_version=POLICY_VERSION,
    )
    assert key == again
    action = ProposedAction(
        proposal_id="proposal_read_readme_001",
        kind=kind,
        summary="Read README",
        intent=intent,
        artifact_refs=["workspace://root/README.md"],
        idempotency_key=key,
    )
    assert action.kind is kind


@pytest.mark.parametrize("marker", SHELL_INTENT_MARKERS)
def test_proposed_action_rejects_shell_intent(marker: str) -> None:
    key = compute_idempotency_key(
        uuid4(),
        uuid4(),
        kind=ProposedActionKind.RUN_COMMAND,
        intent="safe intent",
        artifact_refs=[],
        policy_version=POLICY_VERSION,
    )
    with pytest.raises(ValidationError):
        ProposedAction(
            proposal_id="proposal_shell_001",
            kind=ProposedActionKind.RUN_COMMAND,
            summary="Run tests",
            intent=f"pytest -q{marker} extra",
            artifact_refs=[],
            idempotency_key=key,
        )


def test_proposed_action_rejects_non_hex_idempotency_key() -> None:
    with pytest.raises(ValidationError):
        ProposedAction(
            proposal_id="proposal_bad_key_001",
            kind=ProposedActionKind.REQUEST_REVIEW,
            summary="Review",
            intent="Ask for review",
            artifact_refs=[],
            idempotency_key="z" * 64,
        )


def test_ingest_archives_on_policy_snapshot_mismatch() -> None:
    raw = envelope_base(
        signal_type="routing.recommendation",
        signal_kind="routing.recommendation",
        memory_class="prompt_injectable",
        payload={
            "recommended_cognitive_role": "defect_analysis",
            "rationale": "Looks like a regression.",
            "alternatives_considered": [],
        },
    )
    raw["evidence"] = {"status": "partial", "refs": []}
    now = datetime(2026, 9, 8, 21, 10, tzinfo=UTC)
    mismatch = ingest_cognitive_signal(
        raw,
        now=now,
        active_policy_snapshot_hash="sha256:" + ("0" * 64),
    )
    assert mismatch.accepted is True
    assert mismatch.prompt_eligible is False
    assert mismatch.archive_only is True
    assert mismatch.reason == "policy_snapshot_mismatch"
    matched = ingest_cognitive_signal(
        raw,
        now=now,
        active_policy_snapshot_hash=raw["policy_snapshot_hash"],
    )
    assert matched.prompt_eligible is True


def test_producer_id_covers_built_in_agents() -> None:
    assert producer_id_for_agent("chat") == "mangomas.chat.v2"
    assert producer_id_for_agent("summarize") == "mangomas.summarize.v2"
    assert producer_id_for_agent("tool") == "mangomas.tool.v2"


def test_map_requested_review_roles_empty() -> None:
    assert map_requested_review_roles([]) == []


@pytest.mark.parametrize("role", sorted(FORBIDDEN_RECOMMENDED_ROLES))
def test_routing_payload_rejects_execution_roles(role: str) -> None:
    raw = envelope_base(
        signal_type="routing.recommendation",
        signal_kind="routing.recommendation",
        memory_class="prompt_injectable",
        payload={
            "recommended_cognitive_role": role,
            "rationale": "Would grant write capability if mapped.",
            "alternatives_considered": [],
        },
    )
    raw["evidence"] = {"status": "partial", "refs": []}
    result = ingest_cognitive_signal(raw)
    assert result.accepted is False
    assert result.signal is None


def test_workflow_complete_with_recommendation_ingests() -> None:
    raw = envelope_base(
        signal_type=SignalKind.WORKFLOW_COMPLETE_RECOMMENDED.value,
        signal_kind=SignalKind.WORKFLOW_COMPLETE_RECOMMENDED.value,
        payload={},
    )
    raw["recommendation"] = {
        "disposition": "recommend_stop",
        "summary": "Recommend completion after gates pass.",
        "rationale": "Still advisory.",
        "proposed_action_refs": [],
        "requested_review_roles": [],
    }
    result = ingest_cognitive_signal(raw)
    assert result.accepted is True
    assert result.archive_only is True
