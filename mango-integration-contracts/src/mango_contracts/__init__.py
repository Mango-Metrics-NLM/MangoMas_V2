"""Standalone integration contracts for the cognitive/execution boundary.

Mango-Mas V2 emits these records. The Code Agent Harness validates, archives,
and independently decides whether any action is permissible. Neither system
imports the other's internals.

This package must not import ``mangomas``.
"""

from __future__ import annotations

from mango_contracts.cognitive_signal import (
    SCHEMA_VERSION as SCHEMA_VERSION,
)
from mango_contracts.cognitive_signal import (
    CognitiveRecommendation as CognitiveRecommendation,
)
from mango_contracts.cognitive_signal import (
    CognitiveSignal as CognitiveSignal,
)
from mango_contracts.cognitive_signal import (
    SignalLineage as SignalLineage,
)
from mango_contracts.enums import (
    EvidenceStatus as EvidenceStatus,
)
from mango_contracts.enums import (
    MemoryClass as MemoryClass,
)
from mango_contracts.enums import (
    ProposedActionKind as ProposedActionKind,
)
from mango_contracts.enums import (
    RecommendationDisposition as RecommendationDisposition,
)
from mango_contracts.enums import (
    SignalKind as SignalKind,
)
from mango_contracts.enums import (
    SignalSeverity as SignalSeverity,
)
from mango_contracts.evidence import (
    EvidenceBundle as EvidenceBundle,
)
from mango_contracts.evidence import (
    EvidenceReference as EvidenceReference,
)
from mango_contracts.json_schema import (
    cognitive_signal_json_schema as cognitive_signal_json_schema,
)
from mango_contracts.json_schema import (
    proposed_action_json_schema as proposed_action_json_schema,
)
from mango_contracts.proposed_action import (
    ProposedAction as ProposedAction,
)
from mango_contracts.proposed_action import (
    compute_idempotency_key as compute_idempotency_key,
)
from mango_contracts.roles import (
    UnknownProducerError as UnknownProducerError,
)
from mango_contracts.roles import (
    UnknownReviewRoleError as UnknownReviewRoleError,
)
from mango_contracts.roles import (
    map_requested_review_roles as map_requested_review_roles,
)
from mango_contracts.roles import (
    producer_id_for_agent as producer_id_for_agent,
)
from mango_contracts.validation import (
    SignalIngestionResult as SignalIngestionResult,
)
from mango_contracts.validation import (
    create_review_work_item as create_review_work_item,
)
from mango_contracts.validation import (
    ingest_cognitive_signal as ingest_cognitive_signal,
)
from mango_contracts.validation import (
    policy_input_from_signal as policy_input_from_signal,
)
from mango_contracts.validation import (
    validate_signal_payload as validate_signal_payload,
)

__all__ = [
    "SCHEMA_VERSION",
    "CognitiveRecommendation",
    "CognitiveSignal",
    "EvidenceBundle",
    "EvidenceReference",
    "EvidenceStatus",
    "MemoryClass",
    "ProposedAction",
    "ProposedActionKind",
    "RecommendationDisposition",
    "SignalIngestionResult",
    "SignalKind",
    "SignalLineage",
    "SignalSeverity",
    "UnknownProducerError",
    "UnknownReviewRoleError",
    "cognitive_signal_json_schema",
    "compute_idempotency_key",
    "create_review_work_item",
    "ingest_cognitive_signal",
    "map_requested_review_roles",
    "policy_input_from_signal",
    "producer_id_for_agent",
    "proposed_action_json_schema",
    "validate_signal_payload",
]
