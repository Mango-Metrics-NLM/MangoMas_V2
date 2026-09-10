"""Shared literals for the contracts suite.

Does not import ``mangomas`` — the contracts package is a decoupled envelope
and the tests keep that boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from mango_contracts import SCHEMA_VERSION, SignalKind
from mango_contracts.roles import AGENT_PRODUCER_IDS

POLICY_SNAPSHOT_HASH = "sha256:a28f0df8e424f3495f4e9f0204c39cbcb5556047cbebf15d7b53b08c4ae68204"
BLOB_CONTENT_HASH = "sha256:1b3d85119bfd51ee8a4da281c79d6a01065483ca1dd575ce34df5aea664e86a0"
POLICY_ID = "mango-code-harness-default"
POLICY_VERSION = "2026.09.08+git.4fd7e8f"
PRODUCER_VERSION = "git:4fd7e8f"
CREATED_AT = datetime(2099, 1, 1, 0, 0, 0, tzinfo=UTC)
SIGNAL_ID = UUID("2e917ec2-a88d-4bd1-b8ff-85f1eb8b1d40")
RUN_ID = UUID("8e4ea278-3988-48a4-883a-38b978faadf1")
TASK_ID = UUID("bb4214e9-2138-47bc-95e4-4ad34ee98d72")
TTL_ONE_DAY = 86_400
TTL_THIRTY_MIN = 1_800


def envelope_base(**overrides: Any) -> dict[str, Any]:
    """A minimal valid review.finding envelope as a mutable dict."""
    created = datetime.now(UTC)
    expires = created + timedelta(seconds=TTL_ONE_DAY)
    raw: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "signal_id": str(SIGNAL_ID),
        "run_id": str(RUN_ID),
        "task_id": str(TASK_ID),
        "producer_id": AGENT_PRODUCER_IDS["reviewer"],
        "producer_version": PRODUCER_VERSION,
        "signal_type": SignalKind.REVIEW_FINDING.value,
        "signal_kind": SignalKind.REVIEW_FINDING.value,
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
        "ttl_seconds": TTL_ONE_DAY,
        "policy_id": POLICY_ID,
        "policy_version": POLICY_VERSION,
        "policy_snapshot_hash": POLICY_SNAPSHOT_HASH,
        "summary": "Example finding.",
        "payload": {
            "finding_id": "finding_broker_bypass_001",
            "affected_artifacts": ["workspace://root/example.py"],
            "impact_statement": "Potential direct side effect.",
            "reproduction_notes": [],
        },
    }
    raw.update(overrides)
    return raw
