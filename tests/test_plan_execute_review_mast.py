"""MAST-inspired injected failures on the shipped plan-execute-review graph.

Each case maps onto one of MAST v3's fourteen failure modes (Cemri et al.,
arXiv:2503.13657). The graph still terminates in a typed error or a completed
review — we do not add cells, tools, or a fifteenth "Mango" mode.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tests.constants as constants_facade
from mangomas.agents import PlannerAgent, ReviewerAgent, ToolAgent
from mangomas.agents.planner import ExecutionPlan, PlanStep
from mangomas.agents.reviewer import ReviewResult
from mangomas.config import AgentSettings, LoopSettings
from mangomas.core import AgentContext, AgentRequest, Message, Orchestrator
from mangomas.core.tools import ToolRegistry
from mangomas.errors import StepTimeout, ToolNotFound
from mangomas.registry import Registry
from mangomas.workflow import execute_workflow, load_workflow
from tests.constants import (
    DEFAULT_TOOL_NAME,
    MAST_FAILURE_MODE_COUNT,
    MAST_FAILURE_MODES,
    MAST_FM_DISOBEY_ROLE_SPECIFICATION,
    MAST_FM_INFORMATION_WITHHOLDING,
    MAST_FM_STEP_REPETITION,
    MAST_FM_UNAWARE_OF_TERMINATION_CONDITIONS,
    PLAN_EXECUTE_REVIEW_AGENTS,
    PLAN_EXECUTE_REVIEW_GRAPH_RELPATH,
    SLOW_AGENT_DELAY_SECONDS,
    TINY_STEP_TIMEOUT_SECONDS,
    UNAUTHORIZED_TOOL_NAME,
)
from tests.constants import mast as mast_mod
from tests.fakes import FakeLLM, FakeTool

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GRAPH_PATH = _REPO_ROOT / PLAN_EXECUTE_REVIEW_GRAPH_RELPATH

_PLAN_JSON = ExecutionPlan(
    goal="ship the release",
    steps=[PlanStep(step=1, description="Build the artefact", agent="tool")],
).model_dump_json()
_DUPLICATE_STEP_PLAN_JSON = ExecutionPlan(
    goal="ship the release",
    steps=[
        PlanStep(step=1, description="Build the artefact", agent="tool"),
        PlanStep(step=2, description="Build the artefact", agent="tool"),
    ],
).model_dump_json()
_TOOL_REPLY = "Build completed; artefact published."
_REVIEW_JSON = ReviewResult(passed=True, score=0.9, feedback="LGTM").model_dump_json()
_FAILED_REVIEW_JSON = ReviewResult(
    passed=False,
    score=0.1,
    feedback="artefact missing",
).model_dump_json()


def test_mast_catalog_is_fourteen_unique_codes() -> None:
    assert MAST_FAILURE_MODE_COUNT == 14
    assert MAST_FAILURE_MODES == (
        "FM-1.1",
        "FM-1.2",
        "FM-1.3",
        "FM-1.4",
        "FM-1.5",
        "FM-2.1",
        "FM-2.2",
        "FM-2.3",
        "FM-2.4",
        "FM-2.5",
        "FM-2.6",
        "FM-3.1",
        "FM-3.2",
        "FM-3.3",
    )
    assert len(set(MAST_FAILURE_MODES)) == 14


def test_mast_codes_are_on_the_constants_facade() -> None:
    for name in mast_mod.__all__:
        assert hasattr(constants_facade, name), name


def _req(content: str = "ship the release") -> AgentRequest:
    return AgentRequest(messages=[Message(role="user", content=content)])


def _validated_orch(
    llm: FakeLLM,
    *,
    loop_settings: LoopSettings | None = None,
    tools: ToolRegistry | None = None,
) -> Orchestrator:
    ctx = AgentContext(llm=llm, repo=None, tools=tools)
    orch = Orchestrator(ctx, loop_settings=loop_settings)
    validating = AgentSettings(validate_output=True)
    orch.register(PlannerAgent(settings=validating))
    orch.register(ToolAgent())
    orch.register(ReviewerAgent(settings=validating))
    return orch


async def test_duplicate_dispatch_reaches_the_same_terminal_state() -> None:
    """FM-1.3 Step repetition: the graph is not a loop; a second dispatch terminates."""
    assert MAST_FM_STEP_REPETITION in MAST_FAILURE_MODES
    graph = load_workflow(str(_GRAPH_PATH))
    llm = FakeLLM(replies=[_PLAN_JSON, _TOOL_REPLY, _REVIEW_JSON] * 2)
    orch = _validated_orch(llm)

    first = await execute_workflow(graph, _req(), orch=orch)
    second = await execute_workflow(graph, _req(), orch=orch)

    assert first.content == _REVIEW_JSON
    assert second.content == _REVIEW_JSON
    assert first.agent == PLAN_EXECUTE_REVIEW_AGENTS[-1]
    assert second.agent == PLAN_EXECUTE_REVIEW_AGENTS[-1]


async def test_repeated_plan_steps_do_not_reenter_the_sequence() -> None:
    """FM-1.3: duplicate plan steps still run planner → tool → reviewer once."""
    assert MAST_FM_STEP_REPETITION in MAST_FAILURE_MODES
    graph = load_workflow(str(_GRAPH_PATH))
    llm = FakeLLM(replies=[_DUPLICATE_STEP_PLAN_JSON, _TOOL_REPLY, _REVIEW_JSON])
    orch = _validated_orch(llm)

    resp = await execute_workflow(graph, _req(), orch=orch)

    assert resp.content == _REVIEW_JSON
    assert len(llm.calls) == len(PLAN_EXECUTE_REVIEW_AGENTS)


async def test_step_timeout_is_a_terminal_typed_error() -> None:
    """FM-1.5 Unaware of termination conditions: per-step budget raises StepTimeout."""
    assert MAST_FM_UNAWARE_OF_TERMINATION_CONDITIONS in MAST_FAILURE_MODES
    graph = load_workflow(str(_GRAPH_PATH))
    llm = FakeLLM(
        replies=[_PLAN_JSON, _TOOL_REPLY, _REVIEW_JSON],
        delay_seconds=SLOW_AGENT_DELAY_SECONDS,
    )
    orch = _validated_orch(
        llm,
        loop_settings=LoopSettings(step_timeout_seconds=TINY_STEP_TIMEOUT_SECONDS),
    )

    with pytest.raises(StepTimeout) as exc_info:
        await execute_workflow(graph, _req(), orch=orch)
    assert exc_info.value.seconds == TINY_STEP_TIMEOUT_SECONDS


async def test_missing_artefact_still_terminates_in_a_review() -> None:
    """FM-2.4 Information withholding: empty tool output, reviewer still finishes."""
    assert MAST_FM_INFORMATION_WITHHOLDING in MAST_FAILURE_MODES
    graph = load_workflow(str(_GRAPH_PATH))
    llm = FakeLLM(replies=[_PLAN_JSON, "", _FAILED_REVIEW_JSON])
    orch = _validated_orch(llm)

    resp = await execute_workflow(graph, _req(), orch=orch)

    assert resp.agent == PLAN_EXECUTE_REVIEW_AGENTS[-1]
    parsed = ReviewResult.model_validate_json(resp.content)
    assert parsed.passed is False


async def test_unauthorized_tool_proposal_raises_tool_not_found() -> None:
    """FM-1.2 Disobey role specification: a write-tool proposal is not executed."""
    assert MAST_FM_DISOBEY_ROLE_SPECIFICATION in MAST_FAILURE_MODES
    graph = load_workflow(str(_GRAPH_PATH))
    tool_json = json.dumps({"tool": UNAUTHORIZED_TOOL_NAME, "arguments": {}})
    llm = FakeLLM(replies=[_PLAN_JSON, f"```json\n{tool_json}\n```"])
    tools: ToolRegistry = Registry("tool")
    tools.register(DEFAULT_TOOL_NAME, FakeTool())
    orch = _validated_orch(llm, tools=tools)

    with pytest.raises(ToolNotFound) as exc_info:
        await execute_workflow(graph, _req(), orch=orch)
    assert exc_info.value.name == UNAUTHORIZED_TOOL_NAME
