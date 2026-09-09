"""Second-stage payload models selected by ``signal_type``.

Unknown types are not given a model: ingestion archives them rather than
inventing executable behaviour.
"""

from __future__ import annotations

from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mango_contracts.enums import SignalKind

SignalPayloadModel: TypeAlias = type[BaseModel]

# Cognitive specialist labels only. Harness execution roles must never appear
# here — mapping ``developer → implementer`` is a capability grant (INV-16).
FORBIDDEN_RECOMMENDED_ROLES: frozenset[str] = frozenset(
    {
        "implementer",
        "destructive",
        "write_file",
        "shell",
    }
)


class RoutingRecommendationPayload(BaseModel):
    """Advisory cognitive-role routing — never a tool or capability grant."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    recommended_cognitive_role: str = Field(
        ...,
        pattern=r"^[a-z][a-z0-9_-]{1,63}$",
    )
    rationale: str = Field(..., min_length=1, max_length=4_000)
    alternatives_considered: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("recommended_cognitive_role")
    @classmethod
    def reject_execution_roles(cls, value: str) -> str:
        if value in FORBIDDEN_RECOMMENDED_ROLES:
            raise ValueError("recommended_cognitive_role must not name a harness execution role")
        return value


class ReviewFindingPayload(BaseModel):
    """A review finding about artifacts — not a veto or merge decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_id: str = Field(..., min_length=8, max_length=128)
    affected_artifacts: list[str] = Field(default_factory=list, max_length=32)
    impact_statement: str = Field(..., min_length=1, max_length=4_000)
    reproduction_notes: list[str] = Field(default_factory=list, max_length=16)
    suggested_remediation: str | None = Field(default=None, max_length=8_000)


class PiiFindingPayload(BaseModel):
    """PII/secret-heuristic finding. Examples must already be redacted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_ref: str = Field(..., min_length=1, max_length=4096)
    detector_name: str = Field(..., min_length=1, max_length=128)
    detector_version: str = Field(..., min_length=1, max_length=128)
    match_class: str = Field(..., min_length=1, max_length=128)
    match_count: int = Field(..., ge=0)
    redacted_examples: list[str] = Field(default_factory=list, max_length=10)


class PlanningProposalPayload(BaseModel):
    """Structured plan sketch. Steps are descriptions, not commands."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    goal: str = Field(..., min_length=1, max_length=4_000)
    steps: list[str] = Field(..., min_length=1, max_length=64)


PAYLOAD_REGISTRY: dict[str, SignalPayloadModel] = {
    SignalKind.ROUTING_RECOMMENDATION.value: RoutingRecommendationPayload,
    SignalKind.REVIEW_FINDING.value: ReviewFindingPayload,
    SignalKind.PII_FINDING.value: PiiFindingPayload,
    SignalKind.PLANNING_PROPOSAL.value: PlanningProposalPayload,
}
