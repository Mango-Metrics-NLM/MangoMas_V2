"""Tests for the shipped canonical planner → tool → reviewer pipeline.

Roadmap item 1.3: (a) the shipped example graph loads and validates; (b) the
pipeline runs end-to-end with structured-output validation on for planner and
reviewer; (c) the failure/back-compat paths of the ``validate_output`` flag.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mangomas.agents import PlannerAgent, ReviewerAgent, ToolAgent
from mangomas.agents.planner import ExecutionPlan, PlanStep
from mangomas.agents.reviewer import ReviewResult
from mangomas.config import AgentSettings
from mangomas.core import AgentContext, AgentRequest, Message, Orchestrator
from mangomas.errors import LLMBadResponse
from mangomas.workflow import execute_workflow, load_workflow
from mangomas.workflow.graph import AgentNode, SequenceNode
from tests.constants import (
    PLAN_EXECUTE_REVIEW_AGENTS,
    PLAN_EXECUTE_REVIEW_GRAPH_NAME,
    PLAN_EXECUTE_REVIEW_GRAPH_RELPATH,
)
from tests.fakes import FakeLLM

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GRAPH_PATH = _REPO_ROOT / PLAN_EXECUTE_REVIEW_GRAPH_RELPATH

_PLAN_JSON = ExecutionPlan(
    goal="ship the release",
    steps=[PlanStep(step=1, description="Build the artefact", agent="tool")],
).model_dump_json()
_TOOL_REPLY = "Build completed; artefact published."
_REVIEW_JSON = ReviewResult(passed=True, score=0.9, feedback="LGTM").model_dump_json()


def _req(content: str = "ship the release") -> AgentRequest:
    return AgentRequest(messages=[Message(role="user", content=content)])


def _validated_orch(llm: FakeLLM) -> Orchestrator:
    """Orchestrator with the canonical roster; validation ON for planner+reviewer."""
    ctx = AgentContext(llm=llm, repo=None)
    orch = Orchestrator(ctx)
    validating = AgentSettings(validate_output=True)
    orch.register(PlannerAgent(settings=validating))
    orch.register(ToolAgent())
    orch.register(ReviewerAgent(settings=validating))
    return orch


# ── (a) the shipped example file loads and validates ──────────────────────────


def test_example_graph_file_exists() -> None:
    assert _GRAPH_PATH.is_file()


def test_example_graph_loads_and_validates() -> None:
    graph = load_workflow(str(_GRAPH_PATH))
    assert graph.name == PLAN_EXECUTE_REVIEW_GRAPH_NAME
    assert isinstance(graph.root, SequenceNode)
    assert all(isinstance(step, AgentNode) for step in graph.root.steps)
    agents = tuple(step.agent for step in graph.root.steps if isinstance(step, AgentNode))
    assert agents == PLAN_EXECUTE_REVIEW_AGENTS


# ── (b) end-to-end pipeline with validation ON ────────────────────────────────


async def test_pipeline_end_to_end_with_validation_on() -> None:
    llm = FakeLLM(replies=[_PLAN_JSON, _TOOL_REPLY, _REVIEW_JSON])
    orch = _validated_orch(llm)

    resp = await orch.dispatch_pipeline(list(PLAN_EXECUTE_REVIEW_AGENTS), _req())

    assert resp.agent == "reviewer"
    # Valid output is byte-for-byte the raw LLM content even with validation on.
    assert resp.content == _REVIEW_JSON
    # parse() round-trips the final content back into the schema model.
    reviewer = ReviewerAgent()
    parsed = reviewer.parse(resp.content)
    assert isinstance(parsed, ReviewResult)
    assert parsed == ReviewResult.model_validate_json(_REVIEW_JSON)
    # The planner leg emitted a plan the planner's own parse() accepts, and it
    # was threaded to the tool agent as its input.
    assert isinstance(PlannerAgent().parse(_PLAN_JSON), ExecutionPlan)
    assert llm.calls[1][-1].content == _PLAN_JSON


async def test_example_graph_executes_like_the_pipeline() -> None:
    """The shipped graph is an all-agent sequence, so executing it equals
    ``dispatch_pipeline`` over the same roster (metadata-transparent)."""
    graph = load_workflow(str(_GRAPH_PATH))
    llm = FakeLLM(replies=[_PLAN_JSON, _TOOL_REPLY, _REVIEW_JSON])
    orch = _validated_orch(llm)

    resp = await execute_workflow(graph, _req(), orch=orch)

    assert resp.agent == "reviewer"
    assert resp.content == _REVIEW_JSON


# ── (c) failure path + backwards-compat proof ─────────────────────────────────


async def test_pipeline_invalid_review_with_validation_on_raises() -> None:
    llm = FakeLLM(replies=[_PLAN_JSON, _TOOL_REPLY, "not a review"])
    orch = _validated_orch(llm)
    with pytest.raises(LLMBadResponse):
        await orch.dispatch_pipeline(list(PLAN_EXECUTE_REVIEW_AGENTS), _req())


async def test_pipeline_invalid_review_with_flag_off_passes_through() -> None:
    """With ``validate_output`` at its default (off), malformed reviewer output
    is returned unchanged — the pre-1.3 contract, byte-for-byte."""
    llm = FakeLLM(replies=[_PLAN_JSON, _TOOL_REPLY, "not a review"])
    ctx = AgentContext(llm=llm, repo=None)
    orch = Orchestrator(ctx)
    orch.register(PlannerAgent())
    orch.register(ToolAgent())
    orch.register(ReviewerAgent())

    resp = await orch.dispatch_pipeline(list(PLAN_EXECUTE_REVIEW_AGENTS), _req())

    assert resp.content == "not a review"
    assert resp.agent == "reviewer"
