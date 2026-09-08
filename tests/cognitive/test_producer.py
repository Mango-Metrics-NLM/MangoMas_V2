"""Planner/reviewer emission: flag-off identity, contained failures."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID

from tests.fakes import FakeCognitiveSink, FakeLLM
from tests.mango_contracts.constants import RUN_ID, TASK_ID

from mango_contracts import CognitiveSignal, SignalKind
from mango_contracts.validation import POLICY_INPUT_KEYS
from mangomas.agents.chat import ChatAgent
from mangomas.agents.planner import PlannerAgent
from mangomas.agents.reviewer import ReviewerAgent
from mangomas.agents.summarize import SummarizeAgent
from mangomas.cognitive.constants import (
    COGNITIVE_SETTINGS_EXTRAS_KEY,
    COGNITIVE_SINK_EXTRAS_KEY,
    METADATA_RUN_ID,
    METADATA_TASK_ID,
)
from mangomas.cognitive.pdp import pdp_input_from_signal
from mangomas.cognitive.sink import CognitiveSignalSink
from mangomas.config import SignalSettings
from mangomas.core.agent import AgentContext, AgentRequest, Message

_PLAN = json.dumps(
    {"goal": "ship it", "steps": [{"step": 1, "description": "Build", "agent": None}]}
)
_REVIEW = json.dumps(
    {"passed": True, "score": 0.8, "feedback": "Good job.", "suggestions": ["nits"]}
)


def _request() -> AgentRequest:
    return AgentRequest(
        messages=[Message(role="user", content="go")],
        metadata={METADATA_RUN_ID: str(RUN_ID), METADATA_TASK_ID: str(TASK_ID)},
    )


def _wired_ctx(reply: str, sink: FakeCognitiveSink) -> AgentContext:
    settings = SignalSettings(enabled=True, dir=".")
    return AgentContext(
        llm=FakeLLM(reply=reply),
        repo=None,
        extras={
            COGNITIVE_SINK_EXTRAS_KEY: sink,
            COGNITIVE_SETTINGS_EXTRAS_KEY: settings,
        },
    )


def test_fake_sink_satisfies_the_protocol() -> None:
    assert isinstance(FakeCognitiveSink(), CognitiveSignalSink)


async def test_flag_off_planner_response_identical_and_no_emit() -> None:
    llm = FakeLLM(reply=_PLAN)
    ctx = AgentContext(llm=llm, repo=None)
    resp = await PlannerAgent().handle(_request(), ctx)
    assert resp.content == _PLAN
    assert resp.agent == "planner"
    assert resp.metadata == {}


async def test_flag_off_reviewer_response_identical() -> None:
    llm = FakeLLM(reply=_REVIEW)
    ctx = AgentContext(llm=llm, repo=None)
    resp = await ReviewerAgent().handle(_request(), ctx)
    assert resp.content == _REVIEW
    assert resp.agent == "reviewer"


async def test_flag_on_planner_emits_one_planning_proposal() -> None:
    sink = FakeCognitiveSink()
    ctx = _wired_ctx(_PLAN, sink)
    resp = await PlannerAgent().handle(_request(), ctx)
    assert resp.content == _PLAN
    assert len(sink.emitted) == 1
    signal = sink.emitted[0]
    assert isinstance(signal, CognitiveSignal)
    assert signal.signal_kind is SignalKind.PLANNING_PROPOSAL
    assert signal.producer_id == "mangomas.planner.v2"
    assert signal.payload["goal"] == "ship it"
    assert signal.payload["steps"] == ["Build"]
    assert set(pdp_input_from_signal(signal)) == set(POLICY_INPUT_KEYS)


async def test_failed_review_emits_failed_prefix() -> None:
    sink = FakeCognitiveSink()
    review = json.dumps(
        {"passed": False, "score": 0.2, "feedback": "Needs work.", "suggestions": []}
    )
    ctx = _wired_ctx(review, sink)
    await ReviewerAgent().handle(_request(), ctx)
    assert sink.emitted[0].payload["impact_statement"].startswith("failed:")


async def test_sink_failure_is_contained() -> None:
    sink = FakeCognitiveSink(raise_on_emit=RuntimeError("disk full"))
    ctx = _wired_ctx(_PLAN, sink)
    resp = await PlannerAgent().handle(_request(), ctx)
    assert resp.content == _PLAN
    assert sink.emitted == []


async def test_chat_does_not_emit_even_with_sink() -> None:
    sink = FakeCognitiveSink()
    ctx = _wired_ctx("hello", sink)
    resp = await ChatAgent().handle(_request(), ctx)
    assert resp.content == "hello"
    assert sink.emitted == []


async def test_summarize_does_not_emit_even_with_sink() -> None:
    sink = FakeCognitiveSink()
    ctx = _wired_ctx("summary", sink)
    resp = await SummarizeAgent().handle(_request(), ctx)
    assert resp.content == "summary"
    assert sink.emitted == []


async def test_unparsed_planner_output_still_emits() -> None:
    sink = FakeCognitiveSink()
    ctx = _wired_ctx("not-json", sink)
    await PlannerAgent().handle(_request(), ctx)
    assert sink.emitted[0].payload["steps"] == ["unparsed planner output"]


async def test_invalid_metadata_uuid_mints_new_ids() -> None:
    sink = FakeCognitiveSink()
    settings = SignalSettings(enabled=True, dir=".")
    ctx = AgentContext(
        llm=FakeLLM(reply=_PLAN),
        repo=None,
        extras={
            COGNITIVE_SINK_EXTRAS_KEY: sink,
            COGNITIVE_SETTINGS_EXTRAS_KEY: settings,
        },
    )
    req = AgentRequest(
        messages=[Message(role="user", content="go")],
        metadata={METADATA_RUN_ID: "not-a-uuid", METADATA_TASK_ID: "also-bad"},
    )
    await PlannerAgent().handle(req, ctx)
    signal = sink.emitted[0]
    assert isinstance(signal.run_id, UUID)
    assert str(signal.run_id) != "not-a-uuid"


async def test_two_plans_differing_only_in_confidence_share_pdp_input() -> None:
    """Metamorphic: confidence is observation; PDP projection is identity-only."""
    sink = FakeCognitiveSink()
    ctx = _wired_ctx(_PLAN, sink)
    await PlannerAgent().handle(_request(), ctx)
    signal = sink.emitted[0]
    low = signal.model_copy(update={"confidence": 0.0})
    high = signal.model_copy(update={"confidence": 1.0})
    assert pdp_input_from_signal(low) == pdp_input_from_signal(high)
    assert low.payload == high.payload


def test_flag_off_handle_does_not_import_mango_contracts() -> None:
    """Flag-off handle must not load the envelope package.

    ``_structured.handle`` reads extras and returns; producer/contracts stay
    unimported. A top-level ``import mango_contracts`` in ``_structured`` would
    fail this even when the sink is absent.
    """
    repo = Path(__file__).resolve().parents[2]
    script = (
        "from __future__ import annotations\n"
        "import asyncio\n"
        "import sys\n"
        "from mangomas.agents.planner import PlannerAgent\n"
        "from mangomas.core.agent import AgentContext, AgentRequest, Message\n"
        "\n"
        "class _LLM:\n"
        "    async def complete(self, messages, **kwargs):\n"
        '        return \'{"goal": "x", "steps": [{"description": "a"}]}\'\n'
        "\n"
        "async def _run() -> None:\n"
        "    ctx = AgentContext(llm=_LLM(), repo=None)\n"
        "    req = AgentRequest(messages=[Message(role='user', content='go')])\n"
        "    await PlannerAgent().handle(req, ctx)\n"
        "\n"
        "asyncio.run(_run())\n"
        "loaded = sorted(\n"
        "    k for k in sys.modules\n"
        "    if k == 'mango_contracts' or k.startswith('mango_contracts.')\n"
        ")\n"
        "if loaded:\n"
        "    raise SystemExit('contracts imported on flag-off handle: ' + ','.join(loaded))\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(repo / "src"),
            str(repo / "mango-integration-contracts" / "src"),
            env.get("PYTHONPATH", ""),
        ]
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        env=env,
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
