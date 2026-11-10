"""`json_field` scorer (spec-0045 B5): ADR-0034 semantics as a scorer.

The eval and workflow packages are kept independent by import-linter, so the
comparison semantics are re-implemented here rather than imported. These tests
replay the predicate suite's discriminating cases (`tests/test_workflow_predicate.py`)
so the two cannot drift apart silently.
"""

from __future__ import annotations

import json

import pytest

from mangomas.agents.planner import ExecutionPlan, PlanStep
from mangomas.agents.reviewer import ReviewResult
from mangomas.errors import ConfigError
from mangomas.eval import scorer_registry
from mangomas.eval.protocol import Scorer, ScorerContext
from mangomas.eval.scorers.json_field import ROW_METADATA_KEY, JsonFieldScorer


def _review(*, passed: bool, score: float) -> str:
    return ReviewResult(passed=passed, score=score, feedback="ok").model_dump_json()


def _plan(steps: int) -> str:
    return (
        ExecutionPlan(
            goal="g", steps=[PlanStep(step=i + 1, description=f"s{i}") for i in range(steps)]
        ).model_dump_json()
        if steps
        else json.dumps({"goal": "g", "steps": []})
    )


def _row_ctx(check: dict[str, object]) -> ScorerContext:
    return ScorerContext(row_metadata={ROW_METADATA_KEY: check})


def test_satisfies_protocol_and_registers() -> None:
    assert isinstance(JsonFieldScorer(check={"path": "passed", "equals": True}), Scorer)
    assert (
        scorer_registry.get("json_field")({"path": "score", "at_least": 0.5}).name == "json_field"
    )


async def test_equals_false_matches_a_rejecting_review() -> None:
    scorer = JsonFieldScorer(check={"path": "passed", "equals": False})
    assert (await scorer.score(_review(passed=False, score=0.1), "")).passed is True
    assert (await scorer.score(_review(passed=True, score=0.9), "")).passed is False


async def test_at_least_and_at_most_straddle_their_thresholds() -> None:
    at_least = JsonFieldScorer(check={"path": "score", "at_least": 0.7})
    assert (await at_least.score(_review(passed=True, score=0.8), "")).passed is True
    assert (await at_least.score(_review(passed=True, score=0.6), "")).passed is False
    at_most = JsonFieldScorer(check={"path": "score", "at_most": 0.3})
    assert (await at_most.score(_review(passed=False, score=0.2), "")).passed is True
    assert (await at_most.score(_review(passed=True, score=0.9), "")).passed is False


async def test_boolean_never_satisfies_a_numeric_threshold_and_equals_keeps_bool_apart() -> None:
    """spec-0032 R4: ``True >= 0.5`` is silently true in Python; it must not be here."""
    numeric = JsonFieldScorer(check={"path": "passed", "at_least": 0.5})
    assert (await numeric.score(_review(passed=True, score=0.9), "")).passed is False
    one = JsonFieldScorer(check={"path": "n", "equals": 1})
    assert (await one.score('{"n": true}', "")).passed is False
    assert (await one.score('{"n": 1}', "")).passed is True
    true = JsonFieldScorer(check={"path": "n", "equals": True})
    assert (await true.score('{"n": 1}', "")).passed is False


async def test_an_integer_too_large_for_a_float_is_not_a_number() -> None:
    """``float(10**400)`` raises OverflowError; the predicate treats that as "not a number"."""
    huge = '{"n": ' + "1" + "0" * 400 + "}"
    assert (
        await JsonFieldScorer(check={"path": "n", "at_least": 1}).score(huge, "")
    ).passed is False


async def test_min_length_is_the_one_additive_comparison() -> None:
    scorer = JsonFieldScorer(check={"path": "steps", "min_length": 1})
    assert (await scorer.score(_plan(2), "")).passed is True
    empty = await scorer.score(_plan(0), "")
    assert empty.passed is False and empty.metadata["found"] == []
    assert (await scorer.score('{"steps": "ab"}', "")).passed is True
    assert (await scorer.score('{"steps": 3}', "")).passed is False, "a number has no length"


async def test_dotted_path_walks_mappings_only() -> None:
    nested = JsonFieldScorer(check={"path": "a.b.c", "equals": 1})
    assert (await nested.score('{"a": {"b": {"c": 1}}}', "")).passed is True
    missing = await nested.score('{"a": {"b": {}}}', "")
    assert missing.passed is False and missing.metadata["error"] == "path_not_found"
    # A list index is not a segment: the trace shape `tool_calls.0.tool` cannot resolve.
    listed = await JsonFieldScorer(check={"path": "calls.0.tool", "equals": "x"}).score(
        '{"calls": [{"tool": "x"}]}', ""
    )
    assert listed.passed is False and listed.metadata["error"] == "path_not_found"


async def test_unparseable_or_non_object_prediction_is_a_failing_row_not_an_error() -> None:
    scorer = JsonFieldScorer(check={"path": "passed", "equals": True})
    for prediction in ("not json", "[1, 2]", '"string"', "", "```json\n{}\n```x"):
        result = await scorer.score(prediction, "")
        assert (result.passed, result.score) == (False, 0.0), prediction
        assert result.metadata["error"] == "not_json_object"


async def test_row_metadata_check_overrides_the_scorer_default() -> None:
    scorer = JsonFieldScorer(check={"path": "passed", "equals": True})
    row = _row_ctx({"path": "score", "at_least": 0.5})
    result = await scorer.score(_review(passed=False, score=0.9), "", context=row)
    assert result.passed is True and result.metadata["path"] == "score"


async def test_no_check_anywhere_is_a_configuration_error() -> None:
    with pytest.raises(ConfigError, match="no check configured"):
        await JsonFieldScorer().score("{}", "")


@pytest.mark.parametrize(
    ("check", "match"),
    [
        ({"equals": 1}, "path"),
        ({"path": "a"}, "exactly one of"),
        ({"path": "a", "equals": 1, "at_least": 1}, "exactly one of"),
        ({"path": "a", "at_least": "high"}, "numeric threshold"),
        ({"path": "a", "min_length": -1}, "non-negative int"),
        ({"path": "a", "min_length": True}, "non-negative int"),
    ],
)
def test_invalid_checks_are_rejected_at_construction(check: dict[str, object], match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        JsonFieldScorer(check=check)
