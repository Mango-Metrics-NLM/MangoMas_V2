"""`composite` scorer (spec-0045 B5): several scorers per row, each recorded, min/all verdict."""

from __future__ import annotations

import json

import pytest

from mangomas.agents import ChatAgent
from mangomas.core import AgentContext, Message, Orchestrator
from mangomas.errors import ConfigError
from mangomas.eval import EvalReport, EvalRunner, evaluate_gate, scorer_registry
from mangomas.eval.dataset import DatasetRow
from mangomas.eval.protocol import Scorer, ScorerContext, ScoreResult
from mangomas.eval.scorers.composite import SCORES_METADATA_KEY, CompositeScorer, build_composite
from mangomas.eval.scorers.contains import ContainsScorer
from mangomas.eval.scorers.exact_match import ExactMatchScorer
from mangomas.eval.targets import AgentTarget
from tests.constants import DEFAULT_AGENT_NAME, EVAL_COST_USD_METADATA_KEY
from tests.fakes import FakeLLM, FakeRepository

_PLAN = json.dumps({"goal": "g", "steps": [{"step": 1, "description": "d"}]})


def test_satisfies_protocol_and_registers() -> None:
    scorer = scorer_registry.get("composite")({"scorers": ["exact_match", "contains"]})
    assert isinstance(scorer, Scorer) and scorer.name == "composite"


async def test_every_child_is_recorded_and_the_verdict_is_min_and_all() -> None:
    scorer = CompositeScorer([ExactMatchScorer(), ContainsScorer()])
    result = await scorer.score("hello world", "hello")
    scores = result.metadata[SCORES_METADATA_KEY]
    assert set(scores) == {"exact_match", "contains"}
    assert scores["contains"]["passed"] is True and scores["exact_match"]["passed"] is False
    assert result.passed is False, "one failing child fails the row"
    assert result.score == 0.0, "min, never a mean that hides the failure"
    assert result.metadata["failed"] == ["exact_match"]


async def test_all_children_passing_passes_with_the_lowest_score() -> None:
    scorer = build_composite(
        [
            {"name": "json_keys", "options": {"required_keys": ["goal", "steps"]}},
            {"name": "json_field", "options": {"path": "steps", "min_length": 1}},
        ]
    )
    result = await scorer.score(_PLAN, "", context=ScorerContext())
    assert (result.passed, result.score) == (True, 1.0)
    assert result.metadata["failed"] == []


def test_children_come_from_the_registry_so_plugins_qualify() -> None:
    names = [child.name for child in build_composite(["exact_match", "regex_match"]).children]
    assert names == ["exact_match", "regex_match"]


@pytest.mark.parametrize(
    ("specs", "match"),
    [
        ([], "at least one"),
        (["exact_match", "exact_match"], "unique"),
        ([{"name": "exact_match", "options": "no"}], "must be an object"),
        ([42], "each child"),
        (["composite"], "nest"),
        (["no_such_scorer"], "no_such_scorer"),
    ],
)
def test_invalid_compositions_are_configuration_errors(specs: list[object], match: str) -> None:
    with pytest.raises((ConfigError, Exception), match=match):
        build_composite(specs)


def test_factory_requires_a_scorers_list() -> None:
    with pytest.raises(ConfigError, match="scorers"):
        scorer_registry.get("composite")({})


# --- cost pass-through (Copilot review on PR #124) ---------------------------
#
# The runner's ``mean_cost_usd`` and the ``max_mean_cost_usd`` gate read the
# row's *top-level* ``cost_usd``. Before the lift a ``cost_budget`` child was
# recorded under ``scores`` only, so composing it with any other scorer left
# the report's cost ``None`` and the gate skipped the cost check with an
# informational note — a silently disabled budget, not a failing one.

_DECLARED_COST_USD = 0.5


async def _composite_cost_report() -> EvalReport:
    orch = Orchestrator(AgentContext(llm=FakeLLM(reply="hello"), repo=FakeRepository()))
    orch.register(ChatAgent())
    row = DatasetRow(
        id="r1",
        messages=[Message(role="user", content="hi")],
        expected="hello",
        metadata={EVAL_COST_USD_METADATA_KEY: _DECLARED_COST_USD},
    )
    scorer = build_composite(["cost_budget", "contains"])
    return await EvalRunner(orch, scorer).run([row], target=AgentTarget(agent=DEFAULT_AGENT_NAME))


async def test_the_cost_child_reaches_the_row_and_the_report() -> None:
    report = await _composite_cost_report()
    row = report.rows[0]
    assert row.passed is True
    assert row.metadata[EVAL_COST_USD_METADATA_KEY] == _DECLARED_COST_USD
    assert row.metadata["source"] == "explicit"
    scores = row.metadata[SCORES_METADATA_KEY]
    assert isinstance(scores, dict)
    assert scores["cost_budget"]["metadata"]["source"] == "explicit"
    assert report.mean_cost_usd == _DECLARED_COST_USD


async def test_the_cost_gate_judges_a_composite_run() -> None:
    report = await _composite_cost_report()
    within = evaluate_gate(report, max_mean_cost_usd=_DECLARED_COST_USD * 2)
    assert within.passed is True
    over = evaluate_gate(report, max_mean_cost_usd=_DECLARED_COST_USD / 2)
    assert over.passed is False
    assert any("mean_cost_usd" in reason for reason in over.reasons), over.reasons


async def test_without_a_cost_child_no_cost_is_invented() -> None:
    result = await CompositeScorer([ExactMatchScorer(), ContainsScorer()]).score("a", "a")
    assert EVAL_COST_USD_METADATA_KEY not in result.metadata
    assert "source" not in result.metadata


class _CostOnlyScorer:
    """A plugin-shaped child that reports a cost but no ``source`` label."""

    name = "cost_only"

    async def score(
        self,
        prediction: str,  # noqa: ARG002 — fixed result
        expected: str,  # noqa: ARG002 — fixed result
        *,
        context: ScorerContext | None = None,  # noqa: ARG002 — fixed result
    ) -> ScoreResult:
        return ScoreResult(score=1.0, passed=True, metadata={EVAL_COST_USD_METADATA_KEY: 0.25})


async def test_a_cost_child_without_a_source_lifts_only_the_cost() -> None:
    result = await CompositeScorer([_CostOnlyScorer(), ExactMatchScorer()]).score("a", "a")
    assert result.metadata[EVAL_COST_USD_METADATA_KEY] == 0.25
    assert "source" not in result.metadata


# --- judge status (spec-0047) ------------------------------------------------


def test_a_judge_child_demotes_the_whole_composite() -> None:
    from mangomas.eval.protocol import JudgeAware  # noqa: PLC0415 — scoped to this test
    from mangomas.eval.scorers.llm_judge import LLMJudgeScorer  # noqa: PLC0415

    composite = CompositeScorer([ExactMatchScorer(), LLMJudgeScorer()])
    assert isinstance(composite, JudgeAware)
    status = composite.judge_status
    assert status is not None and status.calibrated is False
    assert status.scorer == "llm_judge (child of composite)"
    assert CompositeScorer([ExactMatchScorer(), ContainsScorer()]).judge_status is None
