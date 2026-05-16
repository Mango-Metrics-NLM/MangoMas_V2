"""Unit tests for ReviewerAgent.stream() — token flow and buffered fallback."""

from __future__ import annotations

import logging

import pytest

from mangomas.agents.reviewer import ReviewerAgent
from mangomas.core.agent import AgentContext, AgentRequest, Message
from tests.fakes import FakeLLM, NonPingableFakeLLM


async def _drain(agent: ReviewerAgent, ctx: AgentContext, request: AgentRequest) -> list[str]:
    chunks: list[str] = []
    async for token in await agent.stream(request, ctx):
        chunks.append(token)
    return chunks


async def test_reviewer_stream_yields_tokens_when_llm_supports_streaming() -> None:
    llm = FakeLLM(chunks=['{"passed":', " true,", ' "score":0.9,', " ..."])
    ctx = AgentContext(llm=llm, repo=None)
    agent = ReviewerAgent()
    request = AgentRequest(messages=[Message(role="user", content="Review this output.")])

    chunks = await _drain(agent, ctx, request)

    assert chunks == ['{"passed":', " true,", ' "score":0.9,', " ..."]
    assert llm.calls[-1][0].role == "system"


async def test_reviewer_stream_falls_back_to_complete_when_no_stream_support(
    caplog: pytest.LogCaptureFixture,
) -> None:
    llm = NonPingableFakeLLM(reply='{"passed":true,"score":0.8,"feedback":"ok","suggestions":[]}')
    ctx = AgentContext(llm=llm, repo=None)
    agent = ReviewerAgent()
    request = AgentRequest(messages=[Message(role="user", content="Review.")])

    with caplog.at_level(logging.WARNING, logger="mangomas.agents.reviewer"):
        chunks = await _drain(agent, ctx, request)

    assert chunks == [llm.reply]
    fallback_logs = [r for r in caplog.records if "complete() fallback" in r.message]
    assert fallback_logs
    assert fallback_logs[0].agent == "reviewer"  # type: ignore[attr-defined]


async def test_reviewer_stream_preserves_existing_system_message() -> None:
    llm = FakeLLM(chunks=["ok"])
    ctx = AgentContext(llm=llm, repo=None)
    agent = ReviewerAgent()
    request = AgentRequest(
        messages=[
            Message(role="system", content="custom reviewer prompt"),
            Message(role="user", content="Review."),
        ]
    )

    await _drain(agent, ctx, request)

    sent_messages = llm.calls[-1]
    system_messages = [m for m in sent_messages if m.role == "system"]
    assert len(system_messages) == 1
    assert system_messages[0].content == "custom reviewer prompt"
