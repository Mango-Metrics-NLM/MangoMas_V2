"""Deterministic agent lanes over the committed datasets (spec-0045 B7).

Each row of ``evals/datasets/*_v1.jsonl`` carries its own LLM transcript in
``metadata.fixture.llm_replies`` and, for the tool agent, the tool roster in
``metadata.fixture.tools``. The lane replays every row through the *real*
agent — planner / reviewer parse-and-validate, ``ToolAgent``'s policy and
trace — with a scripted ``FakeLLM``, scores it with the dataset's scorer, and
diffs the result against the committed baseline. ``MUT-EVAL-03`` corrupts a
planner fixture and requires the planner lane to go red.

What this proves, and what it does not (Copilot review on PR #123): the
replies are fixtures chosen by call index, so the lane cannot see a *prompt
quality* regression — that is the stubbed-LLM / live lane (Tier 2, reliability
plan C3). It does prove the agents' post-processing and refusal paths, the
scorer contract, the baseline mechanics, and — via the outgoing-prompt
assertions — that each agent still sends its schema / tool roster to the model.

Set ``EVAL_LANES_WRITE_BASELINES=1`` to rewrite the baselines instead of
comparing (``make eval-lanes-baseline``); review the diff before committing.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from mangomas.agents import ChatAgent, PlannerAgent, ReviewerAgent, ToolAgent
from mangomas.core import AgentContext, Orchestrator
from mangomas.core.tools import ToolEffects, ToolRegistry
from mangomas.eval import EvalReport, EvalRunner, diff_reports, load_baseline, load_jsonl
from mangomas.eval.dataset import DatasetRow
from mangomas.eval.protocol import Scorer
from mangomas.eval.scorers.composite import build_composite
from mangomas.eval.scorers.tool_trajectory import ToolTrajectoryScorer
from mangomas.eval.sinks.json_file import JsonFileSink
from mangomas.eval.targets import AgentTarget
from mangomas.registry import Registry
from tests.fakes import FakeLLM, FakeRepository, FakeTool

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DATASETS = _REPO_ROOT / "evals" / "datasets"
_BASELINES = _REPO_ROOT / "evals" / "baselines"
_WRITE_BASELINES_ENV = "EVAL_LANES_WRITE_BASELINES"

_AGENTS = {
    "chat": ChatAgent,
    "planner": PlannerAgent,
    "reviewer": ReviewerAgent,
    "tool": ToolAgent,
}
# Tokens each agent's outgoing system prompt must still carry: the structured
# agents render their schema (`build_structured_prompt`), the tool agent its
# roster (`build_tool_system_prompt`). A prompt that lost them would still
# "pass" against scripted replies — these assertions are what makes the lane
# honest about the prompt it exercised.
_PROMPT_TOKENS = {"planner": ('"goal"', '"steps"'), "reviewer": ('"passed"', '"score"')}

# One scorer per dataset; the planner / reviewer lanes grade structure and one
# parsed field, the safety lane grades the tool trajectory.
_SCORERS: dict[str, Scorer] = {
    "planner_v1": build_composite(
        [
            {"name": "json_keys", "options": {"required_keys": ["goal", "steps"]}},
            "json_field",
        ]
    ),
    "reviewer_v1": build_composite(
        [
            {
                "name": "json_keys",
                "options": {"required_keys": ["passed", "score", "feedback", "suggestions"]},
            },
            "json_field",
        ]
    ),
    "safety_toolagent_v1": ToolTrajectoryScorer(),
}
LANES = tuple(sorted(_SCORERS))


def _fixture(row: DatasetRow) -> dict[str, Any]:
    fixture = row.metadata.get("fixture")
    assert isinstance(fixture, dict), f"{row.id}: no fixture block"
    return fixture


def _tool_registry(specs: list[dict[str, Any]]) -> ToolRegistry:
    registry: ToolRegistry = Registry("tool")
    for spec in specs:
        tool = FakeTool(
            name=spec["name"],
            result=spec.get("result", ""),
            effects=ToolEffects(spec.get("effects", ToolEffects.READ_ONLY.value)),
            parameters_schema=dict(spec.get("parameters_schema") or {}),
        )
        registry.register(tool.name, tool)
    return registry


def _orchestrator(row: DatasetRow) -> tuple[Orchestrator, FakeLLM, str]:
    fixture = _fixture(row)
    agent_name = str(fixture["agent"])
    llm = FakeLLM(replies=[str(r) for r in fixture["llm_replies"]])
    tools = _tool_registry(fixture.get("tools") or []) if fixture.get("tools") else None
    orch = Orchestrator(AgentContext(llm=llm, repo=FakeRepository(), tools=tools))
    orch.register(_AGENTS[agent_name]())
    return orch, llm, agent_name


def _merge(reports: list[EvalReport], rows: list[DatasetRow]) -> EvalReport:
    """One report for the lane: per-row results concatenated, counts recomputed."""
    all_rows = [r for report in reports for r in report.rows]
    passed = sum(1 for r in all_rows if r.passed and r.error is None)
    errored = sum(1 for r in all_rows if r.error is not None)
    scored = [r.score for r in all_rows if r.error is None]
    first = reports[0]
    return replace(
        first,
        dataset_size=len(rows),
        passed=passed,
        failed=len(all_rows) - passed - errored,
        errored=errored,
        mean_score=sum(scored) / len(scored) if scored else 0.0,
        duration_ms=sum(r.duration_ms for r in reports),
        rows=all_rows,
        slices={},
    )


async def _run_lane(name: str) -> tuple[EvalReport, list[DatasetRow]]:
    rows = await load_jsonl(_DATASETS / f"{name}.jsonl")
    assert rows, f"{name}: empty dataset"
    reports: list[EvalReport] = []
    for row in rows:
        orch, llm, agent_name = _orchestrator(row)
        runner = EvalRunner(orch, _SCORERS[name], provenance_labels={"lane": name})
        reports.append(await runner.run([row], target=AgentTarget(agent=agent_name)))
        _assert_outgoing_prompt(row, llm, agent_name)
    return _merge(reports, rows), rows


def _assert_outgoing_prompt(row: DatasetRow, llm: FakeLLM, agent_name: str) -> None:
    assert llm.calls, f"{row.id}: the agent never called the model"
    system = "\n".join(m.content for m in llm.calls[0] if m.role == "system")
    for token in _PROMPT_TOKENS.get(agent_name, ()):
        assert token in system, f"{row.id}: {agent_name}'s system prompt lost {token}"
    for spec in _fixture(row).get("tools") or []:
        assert spec["name"] in system, f"{row.id}: tool {spec['name']!r} missing from the prompt"


@pytest.mark.parametrize("lane", LANES)
def test_lane_matches_its_committed_baseline(lane: str) -> None:
    report, rows = asyncio.run(_run_lane(lane))
    baseline_path = _BASELINES / f"{lane}.json"
    if os.environ.get(_WRITE_BASELINES_ENV) == "1":
        asyncio.run(JsonFileSink(path=str(baseline_path)).emit(report))
    failing = [r.row_id for r in report.rows if not r.passed or r.error is not None]
    assert failing == [], f"{lane}: fixtures are written to pass; failing rows: {failing}"
    assert baseline_path.is_file(), f"{lane}: no baseline — run `make eval-lanes-baseline`"
    baseline = asyncio.run(load_baseline(baseline_path))
    diff = diff_reports(baseline, report)
    assert diff.regressed_rows == [], f"{lane}: regressed vs baseline: {diff.regressed_rows}"
    assert diff.dropped_rows == [], f"{lane}: rows vanished vs baseline: {diff.dropped_rows}"
    assert {r.id for r in rows} == {r.row_id for r in baseline.rows} | set(diff.new_rows)


def test_every_committed_dataset_has_a_lane() -> None:
    """A dataset nobody runs is prose; the selfcheck dataset is the CLI's, not this lane's."""
    datasets = {p.stem for p in _DATASETS.glob("*_v1.jsonl")} - {"selfcheck_echo_v1"}
    assert datasets == set(LANES), sorted(datasets ^ set(LANES))


def test_fixture_rows_declare_what_the_lane_needs() -> None:
    for lane in LANES:
        for line in (_DATASETS / f"{lane}.jsonl").read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            raw = json.loads(line)
            fixture = raw["metadata"]["fixture"]
            assert fixture["agent"] in _AGENTS, raw["id"]
            assert fixture["llm_replies"], raw["id"]
            if fixture["agent"] == "tool":
                assert "expected_tools" in raw["metadata"], raw["id"]
            else:
                assert "json_field" in raw["metadata"], raw["id"]
