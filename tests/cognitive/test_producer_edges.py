"""Extra producer branches: genai spans, unparsed review, incomplete extras."""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from tests.constants import PLANNER_SIGNAL_REPLY
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

_PLAN = PLANNER_SIGNAL_REPLY
_EXPORTER: InMemorySpanExporter = InMemorySpanExporter()


def _init_exporter() -> None:
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
        return
    new_provider = TracerProvider()
    new_provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
    trace.set_tracer_provider(new_provider)


_init_exporter()


def _request() -> AgentRequest:
    return AgentRequest(messages=[Message(role="user", content="go")])


def _wired(
    reply: str,
    sink: FakeCognitiveSink,
    *,
    genai_spans: bool = False,
) -> AgentContext:
    settings = SignalSettings(enabled=True, dir=".", genai_spans=genai_spans)
    return AgentContext(
        llm=FakeLLM(reply=reply),
        repo=None,
        extras={
            COGNITIVE_SINK_EXTRAS_KEY: sink,
            COGNITIVE_SETTINGS_EXTRAS_KEY: settings,
        },
    )


async def test_genai_span_emitted_when_enabled() -> None:
    _EXPORTER.clear()
    sink = FakeCognitiveSink()
    await PlannerAgent().handle(_request(), _wired(_PLAN, sink, genai_spans=True))
    names = [span.name for span in _EXPORTER.get_finished_spans()]
    assert GENAI_INVOKE_AGENT_SPAN in names
    events = sink.emitted[0].lineage.source_event_ids
    assert any(item.startswith("otel-trace:") for item in events)


async def test_genai_span_absent_when_disabled() -> None:
    _EXPORTER.clear()
    sink = FakeCognitiveSink()
    await PlannerAgent().handle(_request(), _wired(_PLAN, sink, genai_spans=False))
    names = [span.name for span in _EXPORTER.get_finished_spans()]
    assert GENAI_INVOKE_AGENT_SPAN not in names
    events = sink.emitted[0].lineage.source_event_ids
    assert not any(item.startswith("otel-trace:") for item in events)


async def test_unparsed_review_still_emits() -> None:
    sink = FakeCognitiveSink()
    await ReviewerAgent().handle(_request(), _wired("not-json", sink))
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


async def test_emit_noops_without_sink() -> None:
    ctx = AgentContext(
        llm=FakeLLM(reply=_PLAN),
        repo=None,
        extras={COGNITIVE_SETTINGS_EXTRAS_KEY: SignalSettings(enabled=True, dir=".")},
    )
    await emit_agent_signal(
        agent_name="planner",
        content=_PLAN,
        request=_request(),
        ctx=ctx,
    )


async def test_emit_noops_for_chat_name() -> None:
    sink = FakeCognitiveSink()
    await emit_agent_signal(
        agent_name="chat",
        content="hi",
        request=_request(),
        ctx=_wired("hi", sink),
    )
    assert sink.emitted == []


def test_current_trace_id_outside_span_is_none() -> None:
    assert current_trace_id() is None


async def test_uuid_metadata_is_honoured() -> None:
    sink = FakeCognitiveSink()
    ctx = _wired(_PLAN, sink)
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
    resp = await PlannerAgent().handle(_request(), _wired(_PLAN, sink))
    assert resp.content == _PLAN
    assert sink.emitted == []


async def test_planner_steps_as_strings() -> None:
    sink = FakeCognitiveSink()
    plan = json.dumps({"goal": "ship", "steps": ["Build", "Test"]})
    await PlannerAgent().handle(_request(), _wired(plan, sink))
    assert sink.emitted[0].payload["steps"] == ["Build", "Test"]


async def test_empty_steps_become_unparsed() -> None:
    sink = FakeCognitiveSink()
    plan = json.dumps({"goal": "ship", "steps": []})
    await PlannerAgent().handle(_request(), _wired(plan, sink))
    assert sink.emitted[0].payload["steps"] == ["unparsed planner output"]


async def test_json_array_is_unparsed() -> None:
    sink = FakeCognitiveSink()
    await PlannerAgent().handle(_request(), _wired("[1, 2, 3]", sink))
    assert sink.emitted[0].payload["steps"] == ["unparsed planner output"]


async def test_empty_content_still_emits_valid_payload() -> None:
    sink = FakeCognitiveSink()
    await PlannerAgent().handle(_request(), _wired("", sink))
    assert sink.emitted[0].payload["steps"] == ["unparsed planner output"]


async def test_whitespace_content_still_emits() -> None:
    sink = FakeCognitiveSink()
    await PlannerAgent().handle(_request(), _wired("   \n", sink))
    assert sink.emitted[0].payload["goal"]


async def test_steps_not_a_list_become_unparsed() -> None:
    sink = FakeCognitiveSink()
    plan = json.dumps({"goal": "ship", "steps": "nope"})
    await PlannerAgent().handle(_request(), _wired(plan, sink))
    assert sink.emitted[0].payload["steps"] == ["unparsed planner output"]


async def test_junk_step_items_are_skipped() -> None:
    sink = FakeCognitiveSink()
    plan = json.dumps({"goal": "ship", "steps": [1, None, {"nope": 1}, "ok"]})
    await PlannerAgent().handle(_request(), _wired(plan, sink))
    assert sink.emitted[0].payload["steps"] == ["ok"]


async def test_suggestions_not_a_list_leave_remediation_none() -> None:
    sink = FakeCognitiveSink()
    review = json.dumps({"passed": True, "score": 0.8, "feedback": "ok", "suggestions": "nits"})
    await ReviewerAgent().handle(_request(), _wired(review, sink))
    assert sink.emitted[0].payload["suggested_remediation"] is None


async def test_correlation_id_lands_in_lineage() -> None:
    sink = FakeCognitiveSink()
    set_correlation_id("corr-test-1")
    await PlannerAgent().handle(_request(), _wired(_PLAN, sink))
    events = sink.emitted[0].lineage.source_event_ids
    assert any(item.startswith("mangomas.correlation_id:") for item in events)


def test_current_trace_id_inside_span() -> None:
    tracer = trace.get_tracer("mangomas.cognitive.tests")
    with tracer.start_as_current_span("cognitive.test"):
        value = current_trace_id()
    assert value is not None
    assert len(value) == 32
