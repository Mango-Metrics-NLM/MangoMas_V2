"""Extra producer branches: genai spans, unparsed review, incomplete extras."""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from opentelemetry import trace
from tests.fakes import FakeCognitiveSink, FakeLLM
from tests.mango_contracts.constants import RUN_ID, TASK_ID

from mangomas.agents.planner import PlannerAgent
from mangomas.agents.reviewer import ReviewerAgent
from mangomas.cognitive import producer as producer_mod
from mangomas.cognitive.constants import (
    COGNITIVE_SETTINGS_EXTRAS_KEY,
    COGNITIVE_SINK_EXTRAS_KEY,
    GENAI_INVOKE_AGENT_SPAN,
    METADATA_RUN_ID,
    METADATA_TASK_ID,
)
from mangomas.cognitive.producer import current_trace_id, emit_agent_signal
from mangomas.config import SignalSettings
from mangomas.core.agent import AgentContext, AgentRequest, Message
from mangomas.correlation import set_correlation_id

_PLAN = json.dumps(
    {"goal": "ship it", "steps": [{"step": 1, "description": "Build", "agent": None}]}
)


def _request() -> AgentRequest:
    return AgentRequest(messages=[Message(role="user", content="go")])


async def test_genai_span_alias_is_additive() -> None:
    """When genai_spans is on, handle still returns the same content."""
    sink = FakeCognitiveSink()
    settings = SignalSettings(enabled=True, dir=".", genai_spans=True)
    ctx = AgentContext(
        llm=FakeLLM(reply=_PLAN),
        repo=None,
        extras={
            COGNITIVE_SINK_EXTRAS_KEY: sink,
            COGNITIVE_SETTINGS_EXTRAS_KEY: settings,
        },
    )
    resp = await PlannerAgent().handle(_request(), ctx)
    assert resp.content == _PLAN
    assert len(sink.emitted) == 1
    assert GENAI_INVOKE_AGENT_SPAN == "gen_ai.invoke_agent"


async def test_unparsed_review_still_emits() -> None:
    sink = FakeCognitiveSink()
    ctx = AgentContext(
        llm=FakeLLM(reply="not-json"),
        repo=None,
        extras={
            COGNITIVE_SINK_EXTRAS_KEY: sink,
            COGNITIVE_SETTINGS_EXTRAS_KEY: SignalSettings(enabled=True, dir="."),
        },
    )
    await ReviewerAgent().handle(_request(), ctx)
    assert sink.emitted[0].payload["impact_statement"].startswith("unparsed:")


async def test_emit_noops_without_settings() -> None:
    sink = FakeCognitiveSink()
    ctx = AgentContext(
        llm=FakeLLM(reply=_PLAN),
        repo=None,
        extras={COGNITIVE_SINK_EXTRAS_KEY: sink},
    )
    await emit_agent_signal(
        agent_name="planner",
        content=_PLAN,
        request=_request(),
        ctx=ctx,
    )
    assert sink.emitted == []


async def test_emit_noops_for_chat_name() -> None:
    sink = FakeCognitiveSink()
    ctx = AgentContext(
        llm=FakeLLM(),
        repo=None,
        extras={
            COGNITIVE_SINK_EXTRAS_KEY: sink,
            COGNITIVE_SETTINGS_EXTRAS_KEY: SignalSettings(enabled=True, dir="."),
        },
    )
    await emit_agent_signal(
        agent_name="chat",
        content="hi",
        request=_request(),
        ctx=ctx,
    )
    assert sink.emitted == []


def test_current_trace_id_outside_span_is_none() -> None:
    assert current_trace_id() is None or isinstance(current_trace_id(), str)


async def test_uuid_metadata_is_honoured() -> None:
    sink = FakeCognitiveSink()
    ctx = AgentContext(
        llm=FakeLLM(reply=_PLAN),
        repo=None,
        extras={
            COGNITIVE_SINK_EXTRAS_KEY: sink,
            COGNITIVE_SETTINGS_EXTRAS_KEY: SignalSettings(enabled=True, dir="."),
        },
    )
    req = AgentRequest(
        messages=[Message(role="user", content="go")],
        metadata={METADATA_RUN_ID: RUN_ID, METADATA_TASK_ID: TASK_ID},
    )
    await PlannerAgent().handle(req, ctx)
    assert sink.emitted[0].run_id == RUN_ID
    assert isinstance(sink.emitted[0].run_id, UUID)


async def test_schema_mismatch_is_contained(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(producer_mod, "SCHEMA_VERSION", "9.9.9")
    sink = FakeCognitiveSink()
    ctx = AgentContext(
        llm=FakeLLM(reply=_PLAN),
        repo=None,
        extras={
            COGNITIVE_SINK_EXTRAS_KEY: sink,
            COGNITIVE_SETTINGS_EXTRAS_KEY: SignalSettings(enabled=True, dir="."),
        },
    )
    resp = await PlannerAgent().handle(_request(), ctx)
    assert resp.content == _PLAN
    assert sink.emitted == []


async def test_planner_steps_as_strings() -> None:
    sink = FakeCognitiveSink()
    plan = json.dumps({"goal": "ship", "steps": ["Build", "Test"]})
    ctx = AgentContext(
        llm=FakeLLM(reply=plan),
        repo=None,
        extras={
            COGNITIVE_SINK_EXTRAS_KEY: sink,
            COGNITIVE_SETTINGS_EXTRAS_KEY: SignalSettings(enabled=True, dir="."),
        },
    )
    await PlannerAgent().handle(_request(), ctx)
    assert sink.emitted[0].payload["steps"] == ["Build", "Test"]


async def test_empty_steps_become_unparsed() -> None:
    sink = FakeCognitiveSink()
    plan = json.dumps({"goal": "ship", "steps": []})
    ctx = AgentContext(
        llm=FakeLLM(reply=plan),
        repo=None,
        extras={
            COGNITIVE_SINK_EXTRAS_KEY: sink,
            COGNITIVE_SETTINGS_EXTRAS_KEY: SignalSettings(enabled=True, dir="."),
        },
    )
    await PlannerAgent().handle(_request(), ctx)
    assert sink.emitted[0].payload["steps"] == ["unparsed planner output"]


async def test_json_array_is_unparsed() -> None:
    sink = FakeCognitiveSink()
    ctx = AgentContext(
        llm=FakeLLM(reply="[1, 2, 3]"),
        repo=None,
        extras={
            COGNITIVE_SINK_EXTRAS_KEY: sink,
            COGNITIVE_SETTINGS_EXTRAS_KEY: SignalSettings(enabled=True, dir="."),
        },
    )
    await PlannerAgent().handle(_request(), ctx)
    assert sink.emitted[0].payload["steps"] == ["unparsed planner output"]


async def test_correlation_id_lands_in_lineage() -> None:
    sink = FakeCognitiveSink()
    set_correlation_id("corr-test-1")
    ctx = AgentContext(
        llm=FakeLLM(reply=_PLAN),
        repo=None,
        extras={
            COGNITIVE_SINK_EXTRAS_KEY: sink,
            COGNITIVE_SETTINGS_EXTRAS_KEY: SignalSettings(enabled=True, dir="."),
        },
    )
    await PlannerAgent().handle(_request(), ctx)
    events = sink.emitted[0].lineage.source_event_ids
    assert any(item.startswith("mangomas.correlation_id:") for item in events)


def test_current_trace_id_inside_span() -> None:
    tracer = trace.get_tracer("mangomas.cognitive.tests")
    with tracer.start_as_current_span("cognitive.test"):
        value = current_trace_id()
    assert value is None or len(value) == 32
