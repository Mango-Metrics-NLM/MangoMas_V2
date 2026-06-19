"""Tests for the json_keys scorer."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any, cast

import pytest

import mangomas.eval.scorers  # noqa: F401 — registers scorers
from mangomas.errors import ConfigError
from mangomas.eval import scorer_registry
from mangomas.eval.scorers.json_keys import JsonKeysScorer


async def test_json_keys_all_present_passes() -> None:
    scorer = JsonKeysScorer(required_keys=["a", "b"])
    result = await scorer.score('{"a": 1, "b": 2}', "")
    assert result.passed is True
    assert result.score == 1.0


async def test_json_keys_partial_is_graded() -> None:
    scorer = JsonKeysScorer(required_keys=["a", "b", "c", "d"])
    result = await scorer.score('{"a": 1, "b": 2}', "")
    assert result.score == 0.5
    assert result.passed is False
    assert set(result.metadata["missing"]) == {"c", "d"}


async def test_json_keys_derives_required_from_expected() -> None:
    scorer = JsonKeysScorer()
    result = await scorer.score('{"x": 1, "y": 2}', '{"x": 0, "y": 0}')
    assert result.passed is True


async def test_json_keys_strict_rejects_extra_keys() -> None:
    scorer = JsonKeysScorer(required_keys=["a"], strict=True)
    result = await scorer.score('{"a": 1, "b": 2}', "")
    assert result.score == 0.0
    assert result.metadata["extra"] == ["b"]


async def test_json_keys_malformed_prediction_is_failing_row() -> None:
    scorer = JsonKeysScorer(required_keys=["a"])
    result = await scorer.score("not json", "")
    assert result.passed is False
    assert result.metadata["error"] == "not_json"


async def test_json_keys_non_object_prediction_fails() -> None:
    scorer = JsonKeysScorer(required_keys=["a"])
    result = await scorer.score("[1, 2, 3]", "")
    assert result.metadata["error"] == "not_object"


async def test_json_keys_misconfiguration_raises_config_error() -> None:
    scorer = JsonKeysScorer()  # no required_keys
    with pytest.raises(ConfigError):
        await scorer.score('{"a": 1}', "not-json-expected")


async def test_json_keys_expected_non_object_json_raises_config_error() -> None:
    # ``expected`` is valid JSON but not an object → cannot derive keys.
    scorer = JsonKeysScorer()
    with pytest.raises(ConfigError):
        await scorer.score('{"a": 1}', "[1, 2, 3]")


def test_json_keys_registered() -> None:
    scorer = scorer_registry.get("json_keys")({"required_keys": ["a"], "strict": True})
    assert scorer.name == "json_keys"


# ── Hypothesis fuzz ───────────────────────────────────────────────────────────
_hypothesis = pytest.importorskip("hypothesis")
given = cast(Callable[..., Callable[..., Any]], _hypothesis.given)
settings = cast(Callable[..., Callable[..., Any]], _hypothesis.settings)
st: Any = pytest.importorskip("hypothesis.strategies")

_keys = st.lists(st.text(min_size=1, max_size=8), min_size=1, max_size=6, unique=True)


@settings(max_examples=200)
@given(_keys)
def test_json_keys_full_object_always_scores_one(keys: list[str]) -> None:
    obj = {k: 1 for k in keys}
    scorer = JsonKeysScorer(required_keys=keys)
    result = asyncio.run(scorer.score(json.dumps(obj), ""))
    assert result.score == 1.0
    assert result.passed is True


@settings(max_examples=200)
@given(_keys, st.data())
def test_json_keys_score_in_unit_interval_and_never_raises(
    keys: list[str],
    data: Any,
) -> None:
    # Drop a random subset of keys from the prediction; score must stay graded.
    subset = data.draw(st.lists(st.sampled_from(keys), unique=True))
    obj = {k: 1 for k in subset}
    scorer = JsonKeysScorer(required_keys=keys)
    result = asyncio.run(scorer.score(json.dumps(obj), ""))
    assert 0.0 <= result.score <= 1.0
    assert result.score == len(set(subset)) / len(keys)
