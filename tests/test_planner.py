"""Tests for PlannerAgent: structured plan output and prompt injection."""

from __future__ import annotations

import json

import pydantic
import pytest

from mangomas.agents.planner import ExecutionPlan, PlannerAgent, PlanStep
from mangomas.core.agent import AgentContext, AgentRequest, Message
from tests.fakes import FakeLLM

# ── Model validation ──────────────────────────────────────────────────────────


def test_plan_step_requires_positive_step() -> None:
    with pytest.raises(pydantic.ValidationError):
        PlanStep(step=0, description="bad")


def test_execution_plan_requires_at_least_one_step() -> None:
    with pytest.raises(pydantic.ValidationError):
        ExecutionPlan(goal="do stuff", steps=[])


def test_execution_plan_json_roundtrip() -> None:
    plan = ExecutionPlan(
        goal="Deploy the app",
        steps=[
            PlanStep(step=1, description="Build", agent="tool"),
            PlanStep(step=2, description="Deploy", agent=None),
        ],
    )
    restored = ExecutionPlan.model_validate_json(plan.model_dump_json())
    assert restored == plan


# ── PlannerAgent injects schema into system prompt ────────────────────────────


def test_planner_agent_system_prompt_contains_schema_fields() -> None:
    agent = PlannerAgent()
    # The system prompt is built from the ExecutionPlan schema — must mention key fields.
    assert "goal" in agent._system_prompt
    assert "steps" in agent._system_prompt


def test_planner_agent_custom_prefix_prepended() -> None:
    agent = PlannerAgent(system_prompt="You are a planner.")
    assert agent._system_prompt.startswith("You are a planner.")


# ── PlannerAgent.handle ───────────────────────────────────────────────────────


async def test_planner_agent_returns_llm_content() -> None:
    plan_json = json.dumps(
        {
            "goal": "ship it",
            "steps": [{"step": 1, "description": "Build", "agent": None}],
        }
    )
    llm = FakeLLM(reply=plan_json)
    ctx = AgentContext(llm=llm, repo=None)
    agent = PlannerAgent()
    req = AgentRequest(messages=[Message(role="user", content="Plan the release")])
    resp = await agent.handle(req, ctx)
    assert resp.content == plan_json
    assert resp.agent == "planner"


async def test_planner_agent_injects_system_prompt() -> None:
    llm = FakeLLM(reply="{}")
    ctx = AgentContext(llm=llm, repo=None)
    agent = PlannerAgent()
    req = AgentRequest(messages=[Message(role="user", content="go")])
    await agent.handle(req, ctx)
    first_message = llm.calls[0][0]
    assert first_message.role == "system"


async def test_planner_agent_no_duplicate_system_prompt() -> None:
    llm = FakeLLM(reply="{}")
    ctx = AgentContext(llm=llm, repo=None)
    agent = PlannerAgent()
    req = AgentRequest(
        messages=[
            Message(role="system", content="Existing system."),
            Message(role="user", content="go"),
        ]
    )
    await agent.handle(req, ctx)
    system_msgs = [m for m in llm.calls[0] if m.role == "system"]
    assert len(system_msgs) == 1


async def test_planner_agent_llm_output_parseable_as_plan() -> None:
    plan = ExecutionPlan(
        goal="launch",
        steps=[PlanStep(step=1, description="Prepare", agent="tool")],
    )
    llm = FakeLLM(reply=plan.model_dump_json())
    ctx = AgentContext(llm=llm, repo=None)
    agent = PlannerAgent()
    req = AgentRequest(messages=[Message(role="user", content="Plan")])
    resp = await agent.handle(req, ctx)
    parsed = ExecutionPlan.model_validate_json(resp.content)
    assert parsed.goal == "launch"
    assert parsed.steps[0].agent == "tool"
