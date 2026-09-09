"""Ingestion and PDP projection. Never invokes a broker or grants capability."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from mango_contracts.cognitive_signal import CognitiveSignal
from mango_contracts.payloads import PAYLOAD_REGISTRY

POLICY_INPUT_KEYS: tuple[str, ...] = (
    "run_id",
    "task_id",
    "policy_id",
    "policy_version",
    "policy_snapshot_hash",
)


@dataclass(frozen=True)
class SignalIngestionResult:
    """Outcome of validating a cognitive signal without granting authority."""

    accepted: bool
    archive_only: bool
    prompt_eligible: bool
    reason: str
    signal: CognitiveSignal | None = None


def validate_signal_payload(signal: CognitiveSignal) -> BaseModel | None:
    """Validate payload after the envelope has passed strict validation.

    Unknown ``signal_type`` values have no model and return ``None`` so the
    caller can archive rather than execute.
    """
    payload_model = PAYLOAD_REGISTRY.get(signal.signal_type)
    if payload_model is None:
        return None
    return payload_model.model_validate(signal.payload)


def policy_input_from_signal(signal: CognitiveSignal) -> dict[str, str]:
    """Authority inputs only. Cognitive fields are structurally absent."""
    return {
        "run_id": str(signal.run_id),
        "task_id": str(signal.task_id),
        "policy_id": signal.policy_id,
        "policy_version": signal.policy_version,
        "policy_snapshot_hash": signal.policy_snapshot_hash,
    }


def create_review_work_item(signal: CognitiveSignal) -> dict[str, Any]:
    """Turn an admissible recommendation into an independent work item.

    The item still requires harness authorization. It is not a gate pass.
    """
    if signal.recommendation is None:
        raise ValueError("Signal does not contain a recommendation.")
    return {
        "origin_signal_id": str(signal.signal_id),
        "task_id": str(signal.task_id),
        "requested_disposition": signal.recommendation.disposition.value,
        "summary": signal.recommendation.summary,
        "requires_harness_authorization": True,
    }


def ingest_cognitive_signal(
    raw_signal: dict[str, Any],
    *,
    now: datetime | None = None,
    active_policy_snapshot_hash: str | None = None,
) -> SignalIngestionResult:
    """Validate cognitive output without granting it control authority.

    This function must not invoke ExecutionBroker, modify authorization
    state, change role capabilities, advance a workflow, mark a gate as
    passed, or approve a release.
    """
    current = now if now is not None else datetime.now(UTC)
    try:
        signal = CognitiveSignal.model_validate(raw_signal)
        if signal.signal_type in PAYLOAD_REGISTRY:
            validate_signal_payload(signal)
    except ValidationError as exc:
        return SignalIngestionResult(
            accepted=False,
            archive_only=True,
            prompt_eligible=False,
            reason=f"schema_rejected:{exc.error_count()}_validation_errors",
        )

    if signal.is_expired(current):
        return SignalIngestionResult(
            accepted=True,
            archive_only=True,
            prompt_eligible=False,
            reason="expired_signal_archived",
            signal=signal,
        )

    if (
        active_policy_snapshot_hash is not None
        and signal.policy_snapshot_hash != active_policy_snapshot_hash
    ):
        return SignalIngestionResult(
            accepted=True,
            archive_only=True,
            prompt_eligible=False,
            reason="policy_snapshot_mismatch",
            signal=signal,
        )

    if signal.signal_type not in PAYLOAD_REGISTRY:
        return SignalIngestionResult(
            accepted=True,
            archive_only=True,
            prompt_eligible=False,
            reason="unknown_payload_schema_archived",
            signal=signal,
        )

    eligible = signal.is_prompt_eligible(current)
    return SignalIngestionResult(
        accepted=True,
        archive_only=not eligible,
        prompt_eligible=eligible,
        reason="accepted_as_non_authoritative_cognitive_context",
        signal=signal,
    )
