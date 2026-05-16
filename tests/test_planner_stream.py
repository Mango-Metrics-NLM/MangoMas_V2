"""Unit tests for PlannerAgent.stream() — token flow and buffered fallback."""

from __future__ import annotations

import logging

import pytest

from mangomas.agents.planner import PlannerAgent
from mangomas.core.agent import AgentContext, AgentRequest, Message
from tests.fakes import FakeLLM, NonPingableFakeLLM


async def _drain(agent: PlannerAgent, ctx: AgentContext, request: AgentRequest) -> list[str]:
    chunks: list[str] = []
    async for token in await agent.stream(request, ctx):
        chunks.append(token)
    return chunks


async def test_planner_stream_yields_tokens_when_llm_supports_streaming() -> None:
    llm = FakeLLM(chunks=['{"goal":', ' "deploy",', ' "steps":[{...}]}'])
    ctx = AgentContext(llm=llm, repo=None)
    agent = PlannerAgent()
    request = AgentRequest(messages=[Message(role="user", content="Plan a deploy.")])

    chunks = await _drain(agent, ctx, request)

    assert chunks == ['{"goal":', ' "deploy",', ' "steps":[{...}]}']
    # Schema-aware system prompt must be injected when absent.
    assert llm.calls[-1][0].role == "system"


async def test_planner_stream_falls_back_to_complete_when_no_stream_support(
    caplog: pytest.LogCaptureFixture,
) -> None:
    llm = NonPingableFakeLLM(reply='{"goal":"x","steps":[{"step":1,"description":"y"}]}')
    ctx = AgentContext(llm=llm, repo=None)
    agent = PlannerAgent()
    request = AgentRequest(messages=[Message(role="user", content="Plan something.")])

    with caplog.at_level(logging.WARNING, logger="mangomas.agents.planner"):
        chunks = await _drain(agent, ctx, request)

    assert chunks == [llm.reply], "fallback must deliver the buffered reply as one chunk"
    fallback_logs = [r for r in caplog.records if "complete() fallback" in r.message]
    assert fallback_logs, "expected the buffered-fallback warning to be emitted"
    assert fallback_logs[0].agent == "planner"  # type: ignore[attr-defined]


async def test_planner_stream_preserves_existing_system_message() -> None:
    llm = FakeLLM(chunks=["ok"])
    ctx = AgentContext(llm=llm, repo=None)
    agent = PlannerAgent()
    request = AgentRequest(
        messages=[
            Message(role="system", content="custom system prompt"),
            Message(role="user", content="Plan."),
        ]
    )

    await _drain(agent, ctx, request)

    sent_messages = llm.calls[-1]
    system_messages = [m for m in sent_messages if m.role == "system"]
    assert len(system_messages) == 1
    assert system_messages[0].content == "custom system prompt"
