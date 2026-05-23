"""End-to-end pipeline (planner → tool → reviewer) against LM Studio.

Run with the venv active and LM Studio listening on localhost:1234.

Usage::

    MANGOMAS_LLM__MODEL='google/gemma-4-e4b' \
        python scripts/run_pipeline_e2e.py
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic import ValidationError

from mangomas.agents.planner import ExecutionPlan
from mangomas.agents.reviewer import ReviewResult
from mangomas.composition import build_orchestrator
from mangomas.core.agent import AgentRequest, Message
from mangomas.core.tools import ToolSpec
from mangomas.registry import Registry

logger = logging.getLogger("pipeline_e2e")


# ── Tiny deterministic tools the ToolAgent can call ──────────────────────────


class AddTool:
    name = "add"
    spec = ToolSpec(
        name="add",
        description="Return the sum of two numbers a and b.",
        parameters_schema={
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        },
    )

    async def execute(self, arguments: dict[str, Any]) -> str:
        return json.dumps({"result": float(arguments["a"]) + float(arguments["b"])})


class MultiplyTool:
    name = "multiply"
    spec = ToolSpec(
        name="multiply",
        description="Return the product of two numbers a and b.",
        parameters_schema={
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        },
    )

    async def execute(self, arguments: dict[str, Any]) -> str:
        return json.dumps({"result": float(arguments["a"]) * float(arguments["b"])})


def _hr(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def _try_parse_plan(content: str) -> ExecutionPlan | None:
    try:
        return ExecutionPlan.model_validate_json(content)
    except ValidationError:
        # Some local models wrap JSON in markdown fences — try to recover.
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1:
            try:
                return ExecutionPlan.model_validate_json(content[start : end + 1])
            except ValidationError:
                return None
        return None


def _try_parse_review(content: str) -> ReviewResult | None:
    try:
        return ReviewResult.model_validate_json(content)
    except ValidationError:
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1:
            try:
                return ReviewResult.model_validate_json(content[start : end + 1])
            except ValidationError:
                return None
        return None


async def _run() -> int:
    orch = build_orchestrator()

    # Wire a tool registry into the live context so ToolAgent has something to call.
    tools: Registry = Registry("tools")
    tools.register("add", AddTool())
    tools.register("multiply", MultiplyTool())
    orch.context.tools = tools

    goal = (
        "Compute the area of a rectangle with width 7 and height 4 by calling "
        "the multiply tool, then report the numeric result and a one-sentence "
        "definition of area."
    )

    _hr(f"Goal: {goal}")

    try:
        # ── Step 1: Planner ──────────────────────────────────────────────────
        _hr("Step 1/3 — planner")
        plan_resp = await orch.dispatch(
            "planner",
            AgentRequest(messages=[Message(role="user", content=goal)]),
        )
        print(plan_resp.content)
        plan = _try_parse_plan(plan_resp.content)
        if plan is None:
            print("[plan parse] FAILED — model did not produce valid ExecutionPlan JSON")
        else:
            print(
                f"[plan parse] OK — goal={plan.goal!r}, {len(plan.steps)} step(s):"
            )
            for s in plan.steps:
                print(f"  - step {s.step}: {s.description} (agent={s.agent})")

        # ── Step 2: Tool agent (pipeline-style: plan -> tool input) ──────────
        _hr("Step 2/3 — tool agent")
        tool_input = (
            f"You have tools available. Execute the following plan and return "
            f"only the final numeric answer:\n\n{plan_resp.content}\n\nGoal: {goal}"
        )
        tool_resp = await orch.dispatch(
            "tool",
            AgentRequest(messages=[Message(role="user", content=tool_input)]),
        )
        print(tool_resp.content)
        steps_used = tool_resp.metadata.get("tool_steps")
        print(f"[tool steps used] {steps_used}")

        # ── Step 3: Reviewer ─────────────────────────────────────────────────
        _hr("Step 3/3 — reviewer")
        review_input = (
            f"Goal: {goal}\n\n"
            f"Candidate answer:\n{tool_resp.content}\n\n"
            "Evaluate whether the candidate answer correctly satisfies the goal. "
            "The correct numeric area is 28."
        )
        review_resp = await orch.dispatch(
            "reviewer",
            AgentRequest(messages=[Message(role="user", content=review_input)]),
        )
        print(review_resp.content)
        review = _try_parse_review(review_resp.content)
        if review is None:
            print("[review parse] FAILED — model did not produce valid ReviewResult JSON")
        else:
            print(
                f"[review parse] OK — passed={review.passed} score={review.score:.2f}"
            )
            print(f"  feedback: {review.feedback}")
            for s in review.suggestions:
                print(f"  suggestion: {s}")

        _hr("Summary")
        print(
            json.dumps(
                {
                    "planner_parsed": plan is not None,
                    "tool_steps_used": steps_used,
                    "tool_final_chars": len(tool_resp.content),
                    "reviewer_parsed": review is not None,
                    "reviewer_passed": review.passed if review else None,
                    "reviewer_score": review.score if review else None,
                },
                indent=2,
            )
        )
        return 0
    finally:
        # Mirror the CLI close path so we don't leak the LM Studio httpx pool.
        ctx = orch.context
        if hasattr(ctx.llm, "aclose"):
            await ctx.llm.aclose()
        if ctx.repo is not None:
            if hasattr(ctx.repo, "aclose"):
                await ctx.repo.aclose()
            else:
                ctx.repo.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
