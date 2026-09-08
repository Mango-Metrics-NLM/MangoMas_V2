"""Evidence pointers and bundles accompanying a cognitive conclusion."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mango_contracts.authority import reject_authority_shaped_keys
from mango_contracts.enums import EvidenceStatus

_SHA256_DIGEST = r"^sha256:[a-f0-9]{64}$"


class EvidenceReference(BaseModel):
    """Immutable pointer to input evidence used by a cognitive component."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    evidence_id: str = Field(..., min_length=8, max_length=256)
    source_type: str = Field(..., min_length=2, max_length=64)
    source_uri: str = Field(..., min_length=1, max_length=4096)
    content_hash: str = Field(..., pattern=_SHA256_DIGEST)
    retrieved_at: datetime
    trust_tier: str = Field(..., min_length=2, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_type", "trust_tier")
    @classmethod
    def normalize_classifiers(cls, value: str) -> str:
        return value.strip().lower().replace(" ", "_")

    @field_validator("retrieved_at")
    @classmethod
    def require_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware UTC")
        return value.astimezone(UTC)

    @field_validator("metadata")
    @classmethod
    def reject_authority_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        reject_authority_shaped_keys(value, location="evidence.metadata")
        return value


class EvidenceBundle(BaseModel):
    """Evidence accompanying a cognitive conclusion.

    A signal with ``status=sufficient`` must include at least one immutable
    evidence reference. The harness verifies the reference itself.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: EvidenceStatus = EvidenceStatus.UNKNOWN
    refs: list[EvidenceReference] = Field(default_factory=list, max_length=64)
    missing_evidence: list[str] = Field(default_factory=list, max_length=32)
    limitations: list[str] = Field(default_factory=list, max_length=32)
