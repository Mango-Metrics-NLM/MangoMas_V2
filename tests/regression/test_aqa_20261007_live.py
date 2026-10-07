"""Regression guards for the 2026-10-07 live LM Studio E2E run.

Branch ``sdlc/origin-sync-pr83-aqa-20261006``. Every guard here replays, with a
``FakeLLM``, a failure first captured against a real model, so the suite stays
hermetic and deterministic. Each was verified to fail on the pre-fix code
(``tests.regression`` mutation proof: revert the fix, these go red).

Defect covered (``[trunk]`` = present on ``origin/feat/initial-release``):

  L-D1 [trunk] — ``ToolAgent`` with **no tool registry** (the shipped default:
                 RAG off ⇒ ``ctx.tools is None``) parsed every reply as a
                 potential tool call. No tool prompt is sent in that mode, yet a
                 fenced JSON block without a ``"tool"`` key raised
                 ``LLMBadResponse`` (502). In the shipped
                 ``planner -> tool -> reviewer`` graph the tool hop routinely
                 echoes the planner's JSON plan in a ```json fence, so the
                 advertised pipeline failed on 2 of 3 local models
                 (``liquid/lfm2-24b-a2b`` x3 runs, ``nvidia/nemotron-3-nano-omni``).
                 Previously mis-triaged as a liquid-only flake.

Boundaries pinned alongside the fix (unchanged behaviour):
  * a registry IS configured ⇒ a rejected JSON block still raises (the model
    was given the tool contract, so it is a botched tool call);
  * no registry + a block that DOES parse to a tool call ⇒ ``ToolNotFound``
    (MAST FM-1.2 guard, ``tests/test_tool_agent.py``).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from mangomas.agents import PlannerAgent, ReviewerAgent, ToolAgent
from mangomas.agents.planner import ExecutionPlan, PlanStep
from mangomas.agents.reviewer import ReviewResult
from mangomas.config import AgentSettings
from mangomas.core import AgentContext, AgentRequest, Message, Orchestrator
from mangomas.core.tools import ToolRegistry
from mangomas.errors import LLMBadResponse, ToolNotFound
from mangomas.registry import Registry
from mangomas.workflow import execute_workflow, load_workflow
from tests.constants import (
    DEFAULT_TOOL_NAME,
    PLAN_EXECUTE_REVIEW_AGENTS,
    PLAN_EXECUTE_REVIEW_GRAPH_RELPATH,
)
from tests.fakes import FakeLLM, FakeTool

_TOOL_AGENT_LOGGER = "mangomas.agents.tool_agent"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_GRAPH_PATH = _REPO_ROOT / PLAN_EXECUTE_REVIEW_GRAPH_RELPATH

# The live payload shape (task-1280 traceback): the planner's ExecutionPlan,
# echoed back by the tool hop inside a ```json fence.
_PLAN_JSON = ExecutionPlan(
    goal="Add a health check endpoint to the web service",
    steps=[PlanStep(step=1, description="Expose GET /health", agent="tool")],
).model_dump_json(indent=2)
_REVIEW_JSON = ReviewResult(passed=True, score=0.9, feedback="LGTM").model_dump_json()
_INVALID_JSON_SNIPPET = '{"status": ok, missing quotes}'


def _fenced(body: str) -> str:
    """Wrap *body* the way models emit JSON: prose, then a ```json fence."""
    return f"Here is the plan:\n```json\n{body}\n```"


def _req(content: str = "Add a health check endpoint.") -> AgentRequest:
    return AgentRequest(messages=[Message(role="user", content=content)])


def _registry() -> ToolRegistry:
    reg: ToolRegistry = Registry("tool")
    tool = FakeTool()
    reg.register(tool.name, tool)
    return reg


# ── L-D1: no registry ⇒ rejected JSON is a plain response ─────────────────────


async def test_l_d1_fenced_json_without_tool_key_is_a_plain_reply_without_registry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The exact live failure: fenced plan JSON, no registry → returned, not 502."""
    reply = _fenced(_PLAN_JSON)
    agent = ToolAgent()

    with caplog.at_level(logging.WARNING, logger=_TOOL_AGENT_LOGGER):
        resp = await agent.handle(_req(), AgentContext(llm=FakeLLM(reply=reply), repo=None))

    assert resp.content == reply
    assert resp.metadata == {"tool_steps": 1}
    # The tolerated rejection is visible, with the parser's reason attached.
    records = [r for r in caplog.records if r.name == _TOOL_AGENT_LOGGER]
    assert any("no tool registry configured" in r.getMessage() for r in records)
    assert any("'tool' key" in str(getattr(r, "parse_error", "")) for r in records)


async def test_l_d1_fenced_invalid_json_is_a_plain_reply_without_registry() -> None:
    """A malformed code sample in a chat-style answer is content, not a tool call."""
    reply = _fenced(_INVALID_JSON_SNIPPET)
    resp = await ToolAgent().handle(_req(), AgentContext(llm=FakeLLM(reply=reply), repo=None))
    assert resp.content == reply


async def test_l_d1_boundary_registry_present_still_raises() -> None:
    """With tools registered the model saw the tool contract: rejection stays typed."""
    reply = _fenced(_PLAN_JSON)
    ctx = AgentContext(llm=FakeLLM(reply=reply), repo=None, tools=_registry())
    with pytest.raises(LLMBadResponse, match="'tool' key"):
        await ToolAgent().handle(_req(), ctx)


async def test_l_d1_boundary_valid_tool_call_without_registry_still_not_found() -> None:
    """The MAST FM-1.2 guard is untouched: a real tool call with no registry fails closed."""
    reply = _fenced(json.dumps({"tool": DEFAULT_TOOL_NAME, "arguments": {}}))
    with pytest.raises(ToolNotFound):
        await ToolAgent().handle(_req(), AgentContext(llm=FakeLLM(reply=reply), repo=None))


async def test_l_d1_shipped_graph_completes_when_tool_hop_echoes_fenced_plan() -> None:
    """Graph-level replay of the live run: plan → fenced echo → review, validation on."""
    graph = load_workflow(str(_GRAPH_PATH))
    llm = FakeLLM(replies=[_PLAN_JSON, _fenced(_PLAN_JSON), _REVIEW_JSON])
    orch = Orchestrator(AgentContext(llm=llm, repo=None, tools=None))
    validating = AgentSettings(validate_output=True)
    orch.register(PlannerAgent(settings=validating))
    orch.register(ToolAgent())
    orch.register(ReviewerAgent(settings=validating))

    resp = await execute_workflow(graph, _req(), orch=orch)

    assert resp.agent == PLAN_EXECUTE_REVIEW_AGENTS[-1]
    assert ReviewResult.model_validate_json(resp.content).passed is True
