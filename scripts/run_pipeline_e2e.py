"""End-to-end pipeline (planner → tool → reviewer) against LM Studio.

Run with the venv active and LM Studio listening on the host/port configured
via ``MANGOMAS_LLM__BASE_URL``::

    MANGOMAS_LLM__MODEL='google/gemma-4-e4b' \
        python scripts/run_pipeline_e2e.py

Every tunable (model, base URL, db path, tool list, demo numbers) is
env- or constant-driven; no values are hard-coded into the call sites.
"""

from __future__ import annotations

import asyncio
import json
import logging
import operator
from collections.abc import Callable
from typing import Any

from mangomas.agents.planner import ExecutionPlan
from mangomas.agents.reviewer import ReviewResult
from mangomas.composition import build_orchestrator
from mangomas.core import Orchestrator
from mangomas.core.agent import AgentRequest, Message
from mangomas.core.tools import Tool, ToolSpec, parse_or_recover
from mangomas.errors import ToolExecutionError
from mangomas.registry import Registry

logger = logging.getLogger("pipeline_e2e")

# ── Demo configuration constants ─────────────────────────────────────────────
# Pulling these out of the call sites keeps the script reusable: future demos
# can swap the rectangle, the expected answer, or the tool set without
# touching control flow.

_RECTANGLE_WIDTH: float = 7.0
_RECTANGLE_HEIGHT: float = 4.0
_RECTANGLE_AREA: float = _RECTANGLE_WIDTH * _RECTANGLE_HEIGHT  # 28.0
_HR_WIDTH: int = 78
_TOOL_REGISTRY_NAME: str = "tools"

_GOAL_TEMPLATE: str = (
    "Compute the area of a rectangle with width {w:g} and height {h:g} by "
    "calling the multiply tool, then report the numeric result and a "
    "one-sentence definition of area."
)
_TOOL_INPUT_TEMPLATE: str = (
    "You have tools available. Execute the following plan and return only "
    "the final numeric answer:\n\n{plan}\n\nGoal: {goal}"
)
_REVIEW_INPUT_TEMPLATE: str = (
    "Goal: {goal}\n\nCandidate answer:\n{candidate}\n\n"
    "Evaluate whether the candidate answer correctly satisfies the goal. "
    "The correct numeric area is {expected:g}."
)

_NUMERIC_BINARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "a": {"type": "number"},
        "b": {"type": "number"},
    },
    "required": ["a", "b"],
}


# ── Tiny deterministic tools the ToolAgent can call ──────────────────────────


class BinaryNumericTool:
    """A two-argument numeric tool parametrised by name + operator.

    Replaces the previous ``AddTool``/``MultiplyTool`` duplicates. New
    arithmetic demos just need ``BinaryNumericTool(name, description, op)``.
    """

    def __init__(
        self,
        name: str,
        description: str,
        op: Callable[[float, float], float],
    ) -> None:
        self._name = name
        self._op = op
        self._spec = ToolSpec(
            name=name,
            description=description,
            parameters_schema=_NUMERIC_BINARY_SCHEMA,
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def execute(self, arguments: dict[str, Any]) -> str:
        # LLM may omit a/b or pass non-numeric values despite the schema —
        # surface those as the typed ToolExecutionError so ToolAgent reports
        # a clean error code instead of a raw KeyError/TypeError/ValueError.
        try:
            a = float(arguments["a"])
            b = float(arguments["b"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolExecutionError(
                f"{self._name}: invalid arguments {arguments!r}: {exc}",
                tool_name=self._name,
            ) from exc
        return json.dumps({"result": self._op(a, b)})


def build_demo_tools() -> Registry[Tool]:
    """Build the tool registry used by the demo pipeline.

    Exposed as a top-level builder (not a closure) so unit tests can
    construct + exercise the registry without invoking ``build_orchestrator``.
    """
    registry: Registry[Tool] = Registry(_TOOL_REGISTRY_NAME)
    registry.register("add", BinaryNumericTool("add", "Return a + b.", operator.add))
    registry.register("multiply", BinaryNumericTool("multiply", "Return a * b.", operator.mul))
    return registry


# ── Pretty output helpers ────────────────────────────────────────────────────


def _hr(title: str) -> None:
    print()
    print("=" * _HR_WIDTH)
    print(title)
    print("=" * _HR_WIDTH)


def _emit_plan(plan_resp_content: str) -> ExecutionPlan | None:
    print(plan_resp_content)
    plan = parse_or_recover(plan_resp_content, ExecutionPlan)
    if plan is None:
        print("[plan parse] FAILED — model did not produce valid ExecutionPlan JSON")
        return None
    print(f"[plan parse] OK — goal={plan.goal!r}, {len(plan.steps)} step(s):")
    for step in plan.steps:
        print(f"  - step {step.step}: {step.description} (agent={step.agent})")
    return plan


def _emit_review(review_resp_content: str) -> ReviewResult | None:
    print(review_resp_content)
    review = parse_or_recover(review_resp_content, ReviewResult)
    if review is None:
        print("[review parse] FAILED — model did not produce valid ReviewResult JSON")
        return None
    print(f"[review parse] OK — passed={review.passed} score={review.score:.2f}")
    print(f"  feedback: {review.feedback}")
    for suggestion in review.suggestions:
        print(f"  suggestion: {suggestion}")
    return review


# ── Pipeline stages ──────────────────────────────────────────────────────────


async def run_pipeline(orch: Orchestrator, goal: str) -> dict[str, Any]:
    """Drive planner → tool → reviewer end-to-end and return a summary dict."""
    # ── Step 1: Planner ──────────────────────────────────────────────────────
    _hr("Step 1/3 — planner")
    plan_resp = await orch.dispatch(
        "planner",
        AgentRequest(messages=[Message(role="user", content=goal)]),
    )
    plan = _emit_plan(plan_resp.content)

    # ── Step 2: Tool agent ──────────────────────────────────────────────────
    _hr("Step 2/3 — tool agent")
    tool_input = _TOOL_INPUT_TEMPLATE.format(plan=plan_resp.content, goal=goal)
    tool_resp = await orch.dispatch(
        "tool",
        AgentRequest(messages=[Message(role="user", content=tool_input)]),
    )
    print(tool_resp.content)
    steps_used = tool_resp.metadata.get("tool_steps")
    print(f"[tool steps used] {steps_used}")

    # ── Step 3: Reviewer ────────────────────────────────────────────────────
    _hr("Step 3/3 — reviewer")
    review_input = _REVIEW_INPUT_TEMPLATE.format(
        goal=goal,
        candidate=tool_resp.content,
        expected=_RECTANGLE_AREA,
    )
    review_resp = await orch.dispatch(
        "reviewer",
        AgentRequest(messages=[Message(role="user", content=review_input)]),
    )
    review = _emit_review(review_resp.content)

    return {
        "planner_parsed": plan is not None,
        "tool_steps_used": steps_used,
        "tool_final_chars": len(tool_resp.content),
        "reviewer_parsed": review is not None,
        "reviewer_passed": review.passed if review else None,
        "reviewer_score": review.score if review else None,
    }


async def _main() -> int:
    orch = build_orchestrator()
    orch.context.tools = build_demo_tools()

    goal = _GOAL_TEMPLATE.format(w=_RECTANGLE_WIDTH, h=_RECTANGLE_HEIGHT)
    _hr(f"Goal: {goal}")

    try:
        summary = await run_pipeline(orch, goal)
        _hr("Summary")
        print(json.dumps(summary, indent=2))
        return 0
    finally:
        await orch.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
