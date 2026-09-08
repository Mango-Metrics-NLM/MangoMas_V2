"""Build and emit planner/reviewer CognitiveSignal records.

Failures are logged and swallowed so ``AgentResponse`` is unchanged. The
producer never branches on ``confidence``, ``trace_id``, or ``correlation_id``.
This module must not import ``mangomas.agents`` (layering: cognitive is a
sibling of agents, not a parent).
"""

from __future__ import annotations

import hashlib
import json
import logging
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from opentelemetry import trace
from opentelemetry.trace import Span
from pydantic import BaseModel

from mango_contracts import CognitiveSignal, SignalKind, producer_id_for_agent
from mango_contracts.cognitive_signal import SCHEMA_VERSION, SignalLineage
from mango_contracts.enums import EvidenceStatus
from mango_contracts.evidence import EvidenceBundle, EvidenceReference
from mango_contracts.payloads import PlanningProposalPayload, ReviewFindingPayload
from mango_contracts.validation import validate_signal_payload
from mangomas import __version__ as _PACKAGE_VERSION
from mangomas.cognitive.constants import (
    COGNITIVE_SETTINGS_EXTRAS_KEY,
    COGNITIVE_SINK_EXTRAS_KEY,
    GENAI_INVOKE_AGENT_SPAN,
    METADATA_RUN_ID,
    METADATA_TASK_ID,
)
from mangomas.cognitive.roles import harness_role_for_agent
from mangomas.correlation import get_correlation_id

if TYPE_CHECKING:
    from mangomas.config.signal import SignalSettings
    from mangomas.core.agent import AgentContext, AgentRequest

logger = logging.getLogger(__name__)

_EMITTERS: dict[str, SignalKind] = {
    "planner": SignalKind.PLANNING_PROPOSAL,
    "reviewer": SignalKind.REVIEW_FINDING,
}


def _field_max_length(model: type[BaseModel], name: str) -> int:
    """Read a Pydantic ``max_length`` so producer truncation matches the envelope."""
    for meta in model.model_fields[name].metadata:
        max_length = getattr(meta, "max_length", None)
        if isinstance(max_length, int):
            return max_length
    raise RuntimeError(f"{model.__name__}.{name} is missing max_length metadata")


_SUMMARY_MAX = _field_max_length(CognitiveSignal, "summary")
_IMPACT_MAX = _field_max_length(ReviewFindingPayload, "impact_statement")
_GOAL_MAX = _field_max_length(PlanningProposalPayload, "goal")
_REMEDIATION_MAX = _field_max_length(ReviewFindingPayload, "suggested_remediation")


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _prefixed_sha256(text: str) -> str:
    return "sha256:" + _sha256_hex(text)


def _uuid_from_metadata(metadata: dict[str, Any], key: str) -> UUID:
    raw = metadata.get(key)
    if isinstance(raw, UUID):
        return raw
    if isinstance(raw, str):
        try:
            return UUID(raw)
        except ValueError:
            logger.debug(
                "cognitive metadata id was not a UUID; minting a new one",
                extra={"event": "cognitive_id_mint", "key": key},
            )
    return uuid4()


def current_trace_id() -> str | None:
    """W3C trace id of the current span, or ``None`` outside a valid span."""
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if not ctx.is_valid:
        return None
    return format(ctx.trace_id, "032x")


def _lineage_event_ids() -> list[str]:
    events: list[str] = []
    trace_id = current_trace_id()
    if trace_id:
        events.append(f"otel-trace:{trace_id}")
    correlation_id = get_correlation_id()
    if correlation_id:
        events.append(f"mangomas.correlation_id:{correlation_id}")
    return events


def _as_dict(content: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _planning_payload(content: str) -> dict[str, Any]:
    data = _as_dict(content)
    if data is None:
        goal = (content[:_GOAL_MAX] or "unparsed planner output").strip() or "unparsed"
        return {"goal": goal, "steps": ["unparsed planner output"]}
    goal = str(data.get("goal") or "unparsed")[:_GOAL_MAX].strip() or "unparsed"
    steps: list[str] = []
    raw_steps = data.get("steps")
    if isinstance(raw_steps, list):
        for item in raw_steps:
            if isinstance(item, dict) and item.get("description"):
                steps.append(str(item["description"]))
            elif isinstance(item, str) and item.strip():
                steps.append(item)
    if not steps:
        steps = ["unparsed planner output"]
    return {"goal": goal, "steps": steps}


def _review_payload(content: str) -> dict[str, Any]:
    digest = _sha256_hex(content)[:16]
    data = _as_dict(content)
    if data is None:
        return {
            "finding_id": f"finding_{digest}",
            "affected_artifacts": [],
            "impact_statement": "unparsed: unparsed reviewer output",
            "reproduction_notes": [],
            "suggested_remediation": None,
        }
    passed = bool(data.get("passed"))
    feedback = str(data.get("feedback") or "review")[:_IMPACT_MAX].strip() or "review"
    passed_note = "passed" if passed else "failed"
    suggestions = data.get("suggestions")
    remediation: str | None = None
    if isinstance(suggestions, list):
        joined = "; ".join(str(item) for item in suggestions if item)
        remediation = joined[:_REMEDIATION_MAX] or None
    statement = f"{passed_note}: {feedback}"[:_IMPACT_MAX]
    return {
        "finding_id": f"finding_{digest}",
        "affected_artifacts": [],
        "impact_statement": statement,
        "reproduction_notes": [],
        "suggested_remediation": remediation,
    }


def _evidence_for(agent_name: str, content: str) -> EvidenceBundle:
    digest = _prefixed_sha256(content)
    now = datetime.now(UTC)
    return EvidenceBundle(
        status=EvidenceStatus.PARTIAL,
        refs=[
            EvidenceReference(
                evidence_id=f"output_{_sha256_hex(content)[:16]}",
                source_type="llm_output",
                source_uri=f"mangomas://agent/{agent_name}",
                content_hash=digest,
                retrieved_at=now,
                trust_tier="local_llm",
            )
        ],
    )


def _summary_for(kind: SignalKind, payload: dict[str, Any]) -> str:
    if kind is SignalKind.PLANNING_PROPOSAL:
        text = str(payload.get("goal", "plan"))
    else:
        text = str(payload.get("impact_statement", "review"))
    return (text.strip() or kind.value)[:_SUMMARY_MAX]


def build_signal(
    *,
    agent_name: str,
    content: str,
    request: AgentRequest,
    settings: SignalSettings,
    producer_version: str,
) -> CognitiveSignal:
    """Build a 1.1.0 envelope. Does not emit and does not consult confidence."""
    if settings.schema_version != SCHEMA_VERSION:
        raise ValueError(
            f"MANGOMAS_SIGNAL__SCHEMA_VERSION={settings.schema_version!r} "
            f"does not match contracts {SCHEMA_VERSION}"
        )
    kind = _EMITTERS[agent_name]
    # Observation-role check: raises for unknown / tool. Return value unused —
    # INV-16: the role never selects tools or timeouts.
    harness_role_for_agent(agent_name)
    payload = (
        _planning_payload(content)
        if kind is SignalKind.PLANNING_PROPOSAL
        else _review_payload(content)
    )
    signal = CognitiveSignal.create(
        run_id=_uuid_from_metadata(request.metadata, METADATA_RUN_ID),
        task_id=_uuid_from_metadata(request.metadata, METADATA_TASK_ID),
        producer_id=producer_id_for_agent(agent_name),
        producer_version=producer_version,
        signal_kind=kind,
        summary=_summary_for(kind, payload),
        policy_id=settings.policy_id,
        policy_version=settings.policy_version,
        policy_snapshot_hash=settings.policy_snapshot_hash,
        payload=payload,
        evidence=_evidence_for(agent_name, content),
        # Join keys live in lineage: PlanningProposalPayload / ReviewFindingPayload
        # are extra="forbid", so trace_id must not be smuggled into payload.
        lineage=SignalLineage(source_event_ids=_lineage_event_ids()),
    )
    validate_signal_payload(signal)
    return signal


def _genai_span(settings: SignalSettings) -> AbstractContextManager[Span | None]:
    if not settings.genai_spans:
        return nullcontext()
    tracer = trace.get_tracer(__name__)
    return tracer.start_as_current_span(GENAI_INVOKE_AGENT_SPAN)


async def emit_agent_signal(
    *,
    agent_name: str,
    content: str,
    request: AgentRequest,
    ctx: AgentContext,
) -> None:
    """Emit one signal when a sink is wired. Never raises to the caller."""
    sink = ctx.extras.get(COGNITIVE_SINK_EXTRAS_KEY)
    settings = ctx.extras.get(COGNITIVE_SETTINGS_EXTRAS_KEY)
    if sink is None or settings is None:
        return
    if agent_name not in _EMITTERS:
        return
    with _genai_span(settings) as span:
        if span is not None:
            span.set_attribute("gen_ai.operation.name", "invoke_agent")
            span.set_attribute("gen_ai.agent.name", agent_name)
            span.set_attribute("gen_ai.system", "mangomas")
        try:
            signal = build_signal(
                agent_name=agent_name,
                content=content,
                request=request,
                settings=settings,
                producer_version=_PACKAGE_VERSION,
            )
            await sink.emit(signal)
        except Exception:
            logger.exception(
                "cognitive signal emit failed; dispatch continues",
                extra={"event": "cognitive_emit_failed", "agent": agent_name},
            )
