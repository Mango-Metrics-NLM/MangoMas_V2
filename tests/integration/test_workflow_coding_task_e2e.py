"""E2E workflow test demonstrating a general coding task.

This test uses the ``plan-review-until-passed`` graph structure to simulate
a scenario where the orchestrator receives a coding task, plans it,
writes code via a tool agent, and reviews the outcome.
"""

from __future__ import annotations

import json
from pathlib import Path

from mangomas.agents import PlannerAgent, ReviewerAgent, ToolAgent
from mangomas.agents.planner import ExecutionPlan, PlanStep
from mangomas.agents.reviewer import ReviewResult
from mangomas.composition.agents import STRUCTURED_AGENT_FIELDS
from mangomas.config import AgentSettings
from mangomas.core import AgentRequest, Message, Orchestrator
from mangomas.core.agent import AgentContext
from mangomas.core.tools import ToolRegistry
from mangomas.registry import Registry
from mangomas.workflow import execute_workflow, load_workflow
from tests.constants import PLAN_REVIEW_UNTIL_PASSED_GRAPH_RELPATH
from tests.fakes import FakeLLM, FakeTool

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GRAPH_PATH = _REPO_ROOT / PLAN_REVIEW_UNTIL_PASSED_GRAPH_RELPATH

_CODING_TASK_REQUEST = "Write a Python script to calculate Fibonacci numbers."

_PLAN_JSON = ExecutionPlan(
    goal="Write the requested Python script",
    steps=[PlanStep(step=1, description="Generate fibonacci.py", agent="tool")],
).model_dump_json()

_TOOL_REPLY = (
    "```json\n"
    + json.dumps(
        {
            "tool": "write_to_file",
            "arguments": {
                "filename": "fibonacci.py",
                "content": "def fib(n):\n    return n if n <= 1 else fib(n-1) + fib(n-2)\n",
            },
        }
    )
    + "\n```"
)

_APPROVING_REVIEW = ReviewResult(
    passed=True,
    score=1.0,
    feedback="The function is structurally correct and functionally valid.",
).model_dump_json()


async def test_workflow_orchestrates_coding_task_successfully() -> None:
    """A coding task is planned, executed, and approved by the reviewer."""
    llm = FakeLLM(replies=[_PLAN_JSON, _TOOL_REPLY, "I have written the file.", _APPROVING_REVIEW])
    tool = FakeTool(name="write_to_file", result="File fibonacci.py created successfully.")
    registry: ToolRegistry = Registry("tool")
    registry.register(tool.name, tool)

    ctx = AgentContext(llm=llm, repo=None, tools=registry)
    orch = Orchestrator(ctx)
    validating = AgentSettings(validate_output=True)

    orch.register(PlannerAgent(settings=validating))
    orch.register(ToolAgent())
    orch.register(ReviewerAgent(settings=validating))

    graph = load_workflow(str(_GRAPH_PATH), structured_agents=STRUCTURED_AGENT_FIELDS)
    request = AgentRequest(messages=[Message(role="user", content=_CODING_TASK_REQUEST)])

    response = await execute_workflow(graph, request, orch=orch)

    assert response.agent == "reviewer"
    assert response.content == _APPROVING_REVIEW
    # Validates exactly 4 LLM calls were made:
    # Plan -> Tool (Tool Call) -> Tool (Final Answer) -> Review
    assert len(llm.calls) == 4
