"""Tests for structured planner and reviewer agents."""

from __future__ import annotations

from mangomas.agents import ExecutionPlan, PlannerAgent, ReviewerAgent, ReviewResult
from mangomas.config import AgentSettings
from mangomas.core import AgentContext, AgentRequest, Message
from tests.fakes import FakeLLM


def _request_with_user(content: str = "do the thing") -> AgentRequest:
    return AgentRequest(messages=[Message(role="user", content=content)])


def _request_with_system() -> AgentRequest:
    return AgentRequest(
        messages=[
            Message(role="system", content="existing"),
            Message(role="user", content="do the thing"),
        ]
    )


async def test_planner_agent_default_prompt_and_json_model() -> None:
    llm = FakeLLM(reply='{"goal":"ship","steps":[{"step":1,"description":"test"}]}')
    agent = PlannerAgent()
    response = await agent.handle(_request_with_user(), AgentContext(llm=llm, repo=None))

    assert response.agent == "planner"
    sent = llm.calls[0]
    assert sent[0].role == "system"
    assert "JSON object" in sent[0].content
    plan = ExecutionPlan.model_validate_json(response.content)
    assert plan.goal == "ship"
    assert plan.steps[0].description == "test"


async def test_planner_agent_constructor_prompt_override() -> None:
    llm = FakeLLM(reply='{"goal":"ship","steps":[{"step":1,"description":"test"}]}')
    agent = PlannerAgent(system_prompt="Plan carefully.")
    await agent.handle(_request_with_user(), AgentContext(llm=llm, repo=None))

    assert llm.calls[0][0].content.startswith("Plan carefully.")


async def test_planner_agent_settings_prompt_override() -> None:
    llm = FakeLLM(reply='{"goal":"ship","steps":[{"step":1,"description":"test"}]}')
    agent = PlannerAgent(settings=AgentSettings(system_prompt="Settings planner."))
    await agent.handle(_request_with_user(), AgentContext(llm=llm, repo=None))

    assert llm.calls[0][0].content.startswith("Settings planner.")


async def test_planner_agent_preserves_existing_system_prompt() -> None:
    llm = FakeLLM(reply='{"goal":"ship","steps":[{"step":1,"description":"test"}]}')
    agent = PlannerAgent(system_prompt="unused")
    await agent.handle(_request_with_system(), AgentContext(llm=llm, repo=None))

    assert [message.role for message in llm.calls[0]] == ["system", "user"]
    assert llm.calls[0][0].content == "existing"


async def test_reviewer_agent_default_prompt_and_json_model() -> None:
    llm = FakeLLM(reply='{"passed":true,"score":0.9,"feedback":"good"}')
    agent = ReviewerAgent()
    response = await agent.handle(_request_with_user(), AgentContext(llm=llm, repo=None))

    assert response.agent == "reviewer"
    sent = llm.calls[0]
    assert sent[0].role == "system"
    assert "JSON object" in sent[0].content
    review = ReviewResult.model_validate_json(response.content)
    assert review.passed is True
    assert review.suggestions == []


async def test_reviewer_agent_constructor_prompt_override() -> None:
    llm = FakeLLM(reply='{"passed":true,"score":0.9,"feedback":"good"}')
    agent = ReviewerAgent(system_prompt="Review carefully.")
    await agent.handle(_request_with_user(), AgentContext(llm=llm, repo=None))

    assert llm.calls[0][0].content.startswith("Review carefully.")


async def test_reviewer_agent_settings_prompt_override() -> None:
    llm = FakeLLM(reply='{"passed":true,"score":0.9,"feedback":"good"}')
    agent = ReviewerAgent(settings=AgentSettings(system_prompt="Settings reviewer."))
    await agent.handle(_request_with_user(), AgentContext(llm=llm, repo=None))

    assert llm.calls[0][0].content.startswith("Settings reviewer.")


async def test_reviewer_agent_preserves_existing_system_prompt() -> None:
    llm = FakeLLM(reply='{"passed":true,"score":0.9,"feedback":"good"}')
    agent = ReviewerAgent(system_prompt="unused")
    await agent.handle(_request_with_system(), AgentContext(llm=llm, repo=None))

    assert [message.role for message in llm.calls[0]] == ["system", "user"]
    assert llm.calls[0][0].content == "existing"
