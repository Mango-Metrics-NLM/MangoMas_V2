"""Strict, auditable, non-authoritative cognitive-plane output envelope.

Security boundary:
- Cognitive components can create this record.
- The harness may store, display, summarize, or use it as bounded context.
- The harness must not use confidence, severity, or signal content as a
  direct authorization, capability-grant, execution, completion, or release
  decision input.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mango_contracts.authority import reject_authority_shaped_keys
from mango_contracts.enums import (
    EvidenceStatus,
    MemoryClass,
    RecommendationDisposition,
    SignalKind,
    SignalSeverity,
)
from mango_contracts.evidence import EvidenceBundle

SCHEMA_VERSION: Literal["1.1.0"] = "1.1.0"
SIGNAL_TYPE_PATTERN = r"^[a-z][a-z0-9_-]*\.[a-z][a-z0-9_-]*$"
MAX_TTL_SECONDS = 60 * 60 * 24 * 30
DEFAULT_TTL_SECONDS = 60 * 60 * 24
TTL_SKEW_SECONDS = 5
MAX_TAG_LENGTH = 64
_SHA256_DIGEST = r"^sha256:[a-f0-9]{64}$"
_PRODUCER_ID_PATTERN = r"^[a-z][a-z0-9._-]{2,127}$"
_POLICY_ID_PATTERN = r"^[a-z][a-z0-9._/-]{0,255}$"


class SignalLineage(BaseModel):
    """Causal ancestry without authority over orchestration state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parent_signal_id: UUID | None = None
    causation_id: UUID | None = None
    source_event_ids: list[str] = Field(default_factory=list, max_length=64)


class CognitiveRecommendation(BaseModel):
    """A bounded recommendation only — never authorization or execution."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    disposition: RecommendationDisposition
    summary: str = Field(..., min_length=1, max_length=2_000)
    rationale: str = Field(..., min_length=1, max_length=8_000)
    proposed_action_refs: list[str] = Field(default_factory=list, max_length=32)
    requested_review_roles: list[str] = Field(default_factory=list, max_length=16)


class CognitiveSignal(BaseModel):
    """Versioned envelope. Unknown fields are rejected (``extra='forbid'``)."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    schema_version: Literal["1.1.0"] = SCHEMA_VERSION
    signal_id: UUID
    run_id: UUID
    task_id: UUID
    producer_id: str = Field(
        ...,
        min_length=3,
        max_length=128,
        pattern=_PRODUCER_ID_PATTERN,
    )
    producer_version: str = Field(..., min_length=1, max_length=128)
    signal_type: str = Field(..., pattern=SIGNAL_TYPE_PATTERN)
    signal_kind: SignalKind
    created_at: datetime
    expires_at: datetime
    ttl_seconds: int = Field(default=DEFAULT_TTL_SECONDS, ge=1, le=MAX_TTL_SECONDS)
    policy_id: str = Field(
        ...,
        min_length=1,
        max_length=256,
        pattern=_POLICY_ID_PATTERN,
    )
    policy_version: str = Field(..., min_length=1, max_length=128)
    policy_snapshot_hash: str = Field(..., pattern=_SHA256_DIGEST)
    severity: SignalSeverity = SignalSeverity.INFO
    memory_class: MemoryClass = MemoryClass.ARCHIVE_ONLY
    summary: str = Field(..., min_length=1, max_length=2_000)
    payload: dict[str, Any] = Field(default_factory=dict, max_length=64)
    recommendation: CognitiveRecommendation | None = None
    evidence: EvidenceBundle = Field(default_factory=EvidenceBundle)
    lineage: SignalLineage = Field(default_factory=SignalLineage)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    uncertainty_notes: list[str] = Field(default_factory=list, max_length=32)
    tags: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("signal_type")
    @classmethod
    def normalize_signal_type(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("created_at", "expires_at")
    @classmethod
    def require_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamps must be timezone-aware UTC datetimes.")
        return value.astimezone(UTC)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        normalised: list[str] = []
        for tag in values:
            candidate = tag.strip().lower().replace(" ", "_")
            if not candidate:
                continue
            if len(candidate) > MAX_TAG_LENGTH:
                raise ValueError("Each tag must be 64 characters or fewer.")
            if candidate not in normalised:
                normalised.append(candidate)
        return normalised

    @field_validator("payload")
    @classmethod
    def reject_authority_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        reject_authority_shaped_keys(value, location="payload")
        return value

    @model_validator(mode="after")
    def validate_temporal_evidence_and_kind(self) -> CognitiveSignal:
        if self.signal_type != self.signal_kind.value:
            raise ValueError("signal_type must equal signal_kind")
        expected_expiry = self.created_at + timedelta(seconds=self.ttl_seconds)
        drift = abs((self.expires_at - expected_expiry).total_seconds())
        if drift > TTL_SKEW_SECONDS:
            raise ValueError("expires_at must match created_at + ttl_seconds within five seconds")
        if self.evidence.status == EvidenceStatus.SUFFICIENT and not self.evidence.refs:
            raise ValueError("Evidence status sufficient requires one or more refs")
        self._check_prompt_injectable_contract()
        if (
            self.signal_kind == SignalKind.WORKFLOW_COMPLETE_RECOMMENDED
            and self.recommendation is None
        ):
            raise ValueError("workflow.complete_recommended requires a recommendation record")
        return self

    def _check_prompt_injectable_contract(self) -> None:
        if self.memory_class != MemoryClass.PROMPT_INJECTABLE:
            return
        if self.evidence.status not in {
            EvidenceStatus.PARTIAL,
            EvidenceStatus.SUFFICIENT,
        }:
            raise ValueError("Prompt-injectable signals require partial or sufficient evidence")
        if self.severity == SignalSeverity.CRITICAL:
            raise ValueError("Critical signals cannot become prompt-injectable")

    def is_expired(self, now: datetime | None = None) -> bool:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        return current >= self.expires_at

    def is_prompt_eligible(self, now: datetime | None = None) -> bool:
        """Context-compiler eligibility only — not a permission decision."""
        return (
            not self.is_expired(now)
            and self.memory_class == MemoryClass.PROMPT_INJECTABLE
            and self.evidence.status in {EvidenceStatus.PARTIAL, EvidenceStatus.SUFFICIENT}
        )

    @classmethod
    def create(
        cls,
        *,
        run_id: UUID,
        task_id: UUID,
        producer_id: str,
        producer_version: str,
        signal_kind: SignalKind,
        summary: str,
        policy_id: str,
        policy_version: str,
        policy_snapshot_hash: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        payload: dict[str, Any] | None = None,
        recommendation: CognitiveRecommendation | None = None,
        evidence: EvidenceBundle | None = None,
        lineage: SignalLineage | None = None,
        severity: SignalSeverity = SignalSeverity.INFO,
        memory_class: MemoryClass = MemoryClass.ARCHIVE_ONLY,
        confidence: float | None = None,
        uncertainty_notes: list[str] | None = None,
        tags: list[str] | None = None,
        created_at: datetime | None = None,
        signal_id: UUID | None = None,
    ) -> CognitiveSignal:
        """Stamp ids and UTC timestamps for a producer."""
        created = created_at if created_at is not None else datetime.now(UTC)
        return cls(
            schema_version=SCHEMA_VERSION,
            signal_id=signal_id if signal_id is not None else uuid4(),
            run_id=run_id,
            task_id=task_id,
            producer_id=producer_id,
            producer_version=producer_version,
            signal_type=signal_kind.value,
            signal_kind=signal_kind,
            created_at=created,
            expires_at=created + timedelta(seconds=ttl_seconds),
            ttl_seconds=ttl_seconds,
            policy_id=policy_id,
            policy_version=policy_version,
            policy_snapshot_hash=policy_snapshot_hash,
            severity=severity,
            memory_class=memory_class,
            summary=summary,
            payload=payload or {},
            recommendation=recommendation,
            evidence=evidence if evidence is not None else EvidenceBundle(),
            lineage=lineage if lineage is not None else SignalLineage(),
            confidence=confidence,
            uncertainty_notes=uncertainty_notes or [],
            tags=tags or [],
        )
