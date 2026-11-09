"""`composite` scorer (spec-0045 B5): several scorers per row, each recorded, min/all verdict."""

from __future__ import annotations

import json

import pytest

from mangomas.errors import ConfigError
from mangomas.eval import scorer_registry
from mangomas.eval.protocol import Scorer, ScorerContext
from mangomas.eval.scorers.composite import SCORES_METADATA_KEY, CompositeScorer, build_composite
from mangomas.eval.scorers.contains import ContainsScorer
from mangomas.eval.scorers.exact_match import ExactMatchScorer

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
