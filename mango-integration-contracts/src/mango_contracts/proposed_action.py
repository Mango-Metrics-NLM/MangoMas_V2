"""Separately validated action *proposals*.

A CognitiveSignal may cite ``proposed_action_refs``. Executable detail lives
here, still as intent — never as a shell string, argv, or capability grant.
The harness classifies, authorizes, and brokers independently.
"""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mango_contracts.enums import ProposedActionKind


class ProposedAction(BaseModel):
    """Non-authoritative intent record referenced by a CognitiveSignal."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    proposal_id: str = Field(..., min_length=8, max_length=128)
    kind: ProposedActionKind
    summary: str = Field(..., min_length=1, max_length=2_000)
    intent: str = Field(
        ...,
        min_length=1,
        max_length=4_000,
        description="Natural-language intent. Never an argv or shell program.",
    )
    artifact_refs: list[str] = Field(default_factory=list, max_length=32)
    idempotency_key: str = Field(..., min_length=64, max_length=64)

    @field_validator("idempotency_key")
    @classmethod
    def require_sha256_hex(cls, value: str) -> str:
        if any(char not in "0123456789abcdef" for char in value):
            raise ValueError("idempotency_key must be lowercase sha256 hex")
        return value

    @field_validator("intent")
    @classmethod
    def reject_shell_metacharacters(cls, value: str) -> str:
        if any(marker in value for marker in (";", "|", "`", "$(", "${")):
            raise ValueError("intent must not contain shell metacharacters")
        return value


def compute_idempotency_key(
    run_id: UUID,
    task_id: UUID,
    *,
    kind: ProposedActionKind,
    intent: str,
    artifact_refs: list[str],
    policy_version: str,
) -> str:
    """sha256(run_id, task_id, normalized proposal, policy_version)."""
    canonical = json.dumps(
        {
            "artifact_refs": artifact_refs,
            "intent": intent,
            "kind": kind.value,
            "policy_version": policy_version,
            "run_id": str(run_id),
            "task_id": str(task_id),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
