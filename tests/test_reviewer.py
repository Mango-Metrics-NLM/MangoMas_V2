"""Tests for ReviewerAgent: structured review output and prompt injection."""

from __future__ import annotations

import json

import pydantic
import pytest

from mangomas.agents.reviewer import ReviewerAgent, ReviewResult
from mangomas.core.agent import AgentContext, AgentRequest, Message
from tests.fakes import FakeLLM

# ── Model validation ──────────────────────────────────────────────────────────


def test_review_result_score_bounds() -> None:
    with pytest.raises(pydantic.ValidationError):
        ReviewResult(passed=True, score=1.5, feedback="over")
    with pytest.raises(pydantic.ValidationError):
        ReviewResult(passed=False, score=-0.1, feedback="under")


def test_review_result_json_roundtrip() -> None:
    result = ReviewResult(
        passed=True,
        score=0.9,
        feedback="Excellent.",
        suggestions=["Add tests"],
    )
    restored = ReviewResult.model_validate_json(result.model_dump_json())
    assert restored == result


def test_review_result_suggestions_default_empty() -> None:
    result = ReviewResult(passed=False, score=0.4, feedback="Needs work.")
    assert result.suggestions == []


# ── ReviewerAgent injects schema into system prompt ───────────────────────────


def test_reviewer_agent_system_prompt_contains_schema_fields() -> None:
    agent = ReviewerAgent()
    assert "passed" in agent._system_prompt  # noqa: SLF001
    assert "score" in agent._system_prompt  # noqa: SLF001
    assert "feedback" in agent._system_prompt  # noqa: SLF001


def test_reviewer_agent_custom_prefix_prepended() -> None:
    agent = ReviewerAgent(system_prompt="You are a reviewer.")
    assert agent._system_prompt.startswith("You are a reviewer.")  # noqa: SLF001


# ── ReviewerAgent.handle ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reviewer_agent_returns_llm_content() -> None:
    review_json = json.dumps(
        {"passed": True, "score": 0.8, "feedback": "Good job.", "suggestions": []}
    )
    llm = FakeLLM(reply=review_json)
    ctx = AgentContext(llm=llm, repo=None)
    agent = ReviewerAgent()
    req = AgentRequest(messages=[Message(role="user", content="Review this output")])
    resp = await agent.handle(req, ctx)
    assert resp.content == review_json
    assert resp.agent == "reviewer"


@pytest.mark.asyncio
async def test_reviewer_agent_injects_system_prompt() -> None:
    llm = FakeLLM(reply="{}")
    ctx = AgentContext(llm=llm, repo=None)
    agent = ReviewerAgent()
    req = AgentRequest(messages=[Message(role="user", content="review")])
    await agent.handle(req, ctx)
    first_message = llm.calls[0][0]
    assert first_message.role == "system"


@pytest.mark.asyncio
async def test_reviewer_agent_no_duplicate_system_prompt() -> None:
    llm = FakeLLM(reply="{}")
    ctx = AgentContext(llm=llm, repo=None)
    agent = ReviewerAgent()
    req = AgentRequest(
        messages=[
            Message(role="system", content="Existing system."),
            Message(role="user", content="review"),
        ]
    )
    await agent.handle(req, ctx)
    system_msgs = [m for m in llm.calls[0] if m.role == "system"]
    assert len(system_msgs) == 1


@pytest.mark.asyncio
async def test_reviewer_agent_llm_output_parseable_as_review() -> None:
    result = ReviewResult(
        passed=False,
        score=0.3,
        feedback="Needs more tests.",
        suggestions=["Add edge cases"],
    )
    llm = FakeLLM(reply=result.model_dump_json())
    ctx = AgentContext(llm=llm, repo=None)
    agent = ReviewerAgent()
    req = AgentRequest(messages=[Message(role="user", content="Review")])
    resp = await agent.handle(req, ctx)
    parsed = ReviewResult.model_validate_json(resp.content)
    assert parsed.passed is False
    assert parsed.suggestions == ["Add edge cases"]
