"""JSONL round-trip through the real sink (not the fake)."""

from __future__ import annotations

from pathlib import Path

from tests.constants import PLANNER_SIGNAL_REPLY
from tests.fakes import FakeLLM

from mango_contracts import CognitiveSignal, SignalKind
from mango_contracts.validation import ingest_cognitive_signal
from mangomas.agents.planner import PlannerAgent
from mangomas.cognitive.constants import (
    COGNITIVE_SETTINGS_EXTRAS_KEY,
    COGNITIVE_SINK_EXTRAS_KEY,
    JSONL_FILENAME,
)
from mangomas.cognitive.sink import JsonlCognitiveSink, build_sink
from mangomas.config import SignalSettings
from mangomas.core.agent import AgentContext, AgentRequest, Message

_PLAN = PLANNER_SIGNAL_REPLY


async def test_planner_writes_one_jsonl_line(tmp_path: Path) -> None:
    settings = SignalSettings(enabled=True, dir=str(tmp_path))
    sink = build_sink(settings)
    ctx = AgentContext(
        llm=FakeLLM(reply=_PLAN),
        repo=None,
        extras={
            COGNITIVE_SINK_EXTRAS_KEY: sink,
            COGNITIVE_SETTINGS_EXTRAS_KEY: settings,
        },
    )
    req = AgentRequest(messages=[Message(role="user", content="go")])
    resp = await PlannerAgent().handle(req, ctx)
    assert resp.content == _PLAN
    path = tmp_path / JSONL_FILENAME
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    signal = CognitiveSignal.model_validate_json(lines[0])
    assert signal.signal_kind is SignalKind.PLANNING_PROPOSAL
    ingested = ingest_cognitive_signal(signal.model_dump(mode="json"))
    assert ingested.accepted is True
    assert ingested.signal is not None


def test_blank_http_url_stays_jsonl_only(tmp_path: Path) -> None:
    settings = SignalSettings(enabled=True, dir=str(tmp_path), http_url="   ")
    assert isinstance(build_sink(settings), JsonlCognitiveSink)
