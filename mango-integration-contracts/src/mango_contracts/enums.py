"""Stable enumerations for the cognitive-plane envelope.

None of these values is an authorization, capability grant, or execution
instruction. They classify observations and recommendations only.
"""

from __future__ import annotations

from enum import StrEnum


class SignalKind(StrEnum):
    """Stable top-level classification for cognitive-plane output."""

    ROUTING_RECOMMENDATION = "routing.recommendation"
    PLANNING_PROPOSAL = "planning.proposal"
    RESEARCH_FINDING = "research.finding"
    REVIEW_FINDING = "review.finding"
    SECURITY_FINDING = "security.finding"
    PII_FINDING = "pii.finding"
    QUALITY_FINDING = "quality.finding"
    VALIDATION_HYPOTHESIS = "validation.hypothesis"
    REMEDIATION_RECOMMENDATION = "remediation.recommendation"
    WORKFLOW_BLOCKED = "workflow.blocked"
    WORKFLOW_COMPLETE_RECOMMENDED = "workflow.complete_recommended"
    CONTEXT_SUMMARY = "context.summary"


class SignalSeverity(StrEnum):
    """Signal urgency only; never a gate or authorization input."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class MemoryClass(StrEnum):
    """Storage and injection eligibility — never a permission level."""

    ARCHIVE_ONLY = "archive_only"
    REVIEW_REQUIRED = "review_required"
    PROMPT_INJECTABLE = "prompt_injectable"


class EvidenceStatus(StrEnum):
    """How strongly the cognitive output is grounded."""

    NONE = "none"
    PARTIAL = "partial"
    SUFFICIENT = "sufficient"
    CONFLICTING = "conflicting"
    UNKNOWN = "unknown"


class RecommendationDisposition(StrEnum):
    """The strongest action a cognitive component may *request*.

    These values remain recommendations. The harness independently validates,
    classifies, authorizes, and brokers every real action.
    """

    OBSERVE = "observe"
    REQUEST_REVIEW = "request_review"
    REQUEST_RESEARCH = "request_research"
    REQUEST_REMEDIATION = "request_remediation"
    REQUEST_HUMAN_DECISION = "request_human_decision"
    RECOMMEND_STOP = "recommend_stop"


class ProposedActionKind(StrEnum):
    """Intent class for a separately validated ProposedAction.

    This is not a harness tool name and not a capability grant.
    """

    READ_FILE = "read_file"
    APPLY_PATCH = "apply_patch"
    RUN_COMMAND = "run_command"
    REQUEST_REVIEW = "request_review"
