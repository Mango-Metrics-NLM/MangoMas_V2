"""Lock: planner/reviewer must not emit routing.recommendation (INV-16).

The payload schema is reserved on mango_contracts. This tree proposes
planning/review only; the sibling Code Agent Harness disposes.
"""

from __future__ import annotations

from tests.constants import (
    PLAN_EXECUTE_REVIEW_AGENTS,
    PLANNER_SIGNAL_REPLY,
    REVIEWER_SIGNAL_REPLY,
)
from tests.fakes import FakeCognitiveSink, FakeLLM

from mango_contracts.enums import SignalKind
from mangomas.cognitive.constants import (
    COGNITIVE_SETTINGS_EXTRAS_KEY,
    COGNITIVE_SINK_EXTRAS_KEY,
)
from mangomas.cognitive.producer import _EMITTERS, emit_agent_signal
from mangomas.config import SignalSettings
from mangomas.core.agent import AgentContext, AgentRequest, Message

_PLANNER, _TOOL, _REVIEWER = PLAN_EXECUTE_REVIEW_AGENTS
_ROUTING_AGENT = "router"


def test_emitters_are_planner_and_reviewer_only() -> None:
    assert set(_EMITTERS) == {_PLANNER, _REVIEWER}
    assert _TOOL not in _EMITTERS
    assert set(_EMITTERS.values()) == {
        SignalKind.PLANNING_PROPOSAL,
        SignalKind.REVIEW_FINDING,
    }
    assert SignalKind.ROUTING_RECOMMENDATION not in _EMITTERS.values()


async def test_emit_does_not_produce_routing_recommendation() -> None:
    sink = FakeCognitiveSink()
    ctx = AgentContext(
        llm=FakeLLM(reply=PLANNER_SIGNAL_REPLY),
        repo=None,
        extras={
            COGNITIVE_SINK_EXTRAS_KEY: sink,
            COGNITIVE_SETTINGS_EXTRAS_KEY: SignalSettings(enabled=True, dir="."),
        },
    )
    request = AgentRequest(messages=[Message(role="user", content="go")])
    await emit_agent_signal(
        agent_name=_ROUTING_AGENT,
        content=PLANNER_SIGNAL_REPLY,
        request=request,
        ctx=ctx,
    )
    await emit_agent_signal(
        agent_name=_PLANNER,
        content=PLANNER_SIGNAL_REPLY,
        request=request,
        ctx=ctx,
    )
    await emit_agent_signal(
        agent_name=_REVIEWER,
        content=REVIEWER_SIGNAL_REPLY,
        request=request,
        ctx=ctx,
    )
    assert sink.emitted
    kinds = {signal.signal_kind for signal in sink.emitted}
    assert SignalKind.ROUTING_RECOMMENDATION not in kinds
    assert kinds == {SignalKind.PLANNING_PROPOSAL, SignalKind.REVIEW_FINDING}
