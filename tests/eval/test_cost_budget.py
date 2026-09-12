"""Tests for the cost_budget scorer."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, cast

import pytest

import mangomas.eval.scorers  # noqa: F401 — registers scorers
from mangomas.errors import ConfigError
from mangomas.eval import scorer_registry
from mangomas.eval._options import require_non_negative_float
from mangomas.eval.protocol import ScorerContext
from mangomas.eval.scorers.cost_budget import CostBudgetScorer, estimate_cost_usd
from tests.constants import (
    DEFAULT_EVAL_COST_MAX_USD,
    DEFAULT_EVAL_COST_USD_PER_1K_INPUT_TOKENS,
    DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_CHARS,
    DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_TOKENS,
    EVAL_COST_INPUT_TOKENS_METADATA_KEY,
    EVAL_COST_OUTPUT_TOKENS_METADATA_KEY,
    EVAL_COST_SCORER_NAME,
    EVAL_COST_SOURCE_EXPLICIT,
    EVAL_COST_SOURCE_OUTPUT_CHARS,
    EVAL_COST_SOURCE_TOKENS,
    EVAL_COST_TOKENS_PER_THOUSAND,
    EVAL_COST_USD_METADATA_KEY,
    STUB_REPLY,
)


def _token_cost(input_tokens: float, output_tokens: float) -> float:
    return (
        input_tokens / EVAL_COST_TOKENS_PER_THOUSAND * DEFAULT_EVAL_COST_USD_PER_1K_INPUT_TOKENS
        + output_tokens / EVAL_COST_TOKENS_PER_THOUSAND * DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_TOKENS
    )


def _char_cost(prediction: str) -> float:
    return (
        len(prediction) / EVAL_COST_TOKENS_PER_THOUSAND * DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_CHARS
    )


async def test_measure_only_always_passes() -> None:
    result = await CostBudgetScorer().score(STUB_REPLY, "ignored")
    assert result.passed is True
    assert result.score == 1.0
    assert result.metadata["scorer"] == EVAL_COST_SCORER_NAME
    assert result.metadata[EVAL_COST_USD_METADATA_KEY] >= 0.0
    assert result.metadata["max_cost_usd"] is DEFAULT_EVAL_COST_MAX_USD


async def test_explicit_cost_usd_wins_over_tokens() -> None:
    explicit = 0.42
    ctx = ScorerContext(
        row_metadata={
            EVAL_COST_USD_METADATA_KEY: explicit,
            EVAL_COST_INPUT_TOKENS_METADATA_KEY: 10_000,
            EVAL_COST_OUTPUT_TOKENS_METADATA_KEY: 10_000,
        }
    )
    result = await CostBudgetScorer().score(STUB_REPLY, "", context=ctx)
    assert result.metadata[EVAL_COST_USD_METADATA_KEY] == explicit
    assert result.metadata["source"] == EVAL_COST_SOURCE_EXPLICIT


async def test_token_metadata_beats_character_fallback() -> None:
    input_tokens = 1000.0
    output_tokens = 2000.0
    ctx = ScorerContext(
        row_metadata={
            EVAL_COST_INPUT_TOKENS_METADATA_KEY: input_tokens,
            EVAL_COST_OUTPUT_TOKENS_METADATA_KEY: output_tokens,
        }
    )
    result = await CostBudgetScorer().score("x" * 50_000, "", context=ctx)
    assert result.metadata[EVAL_COST_USD_METADATA_KEY] == pytest.approx(
        _token_cost(input_tokens, output_tokens)
    )
    assert result.metadata["source"] == EVAL_COST_SOURCE_TOKENS


async def test_character_fallback_without_token_metadata() -> None:
    result = await CostBudgetScorer().score(STUB_REPLY, "")
    assert result.metadata[EVAL_COST_USD_METADATA_KEY] == pytest.approx(_char_cost(STUB_REPLY))
    assert result.metadata["source"] == EVAL_COST_SOURCE_OUTPUT_CHARS


async def test_budget_fail_when_over_max() -> None:
    ctx = ScorerContext(row_metadata={EVAL_COST_USD_METADATA_KEY: 1.0})
    result = await CostBudgetScorer(max_cost_usd=0.5).score(STUB_REPLY, "", context=ctx)
    assert result.passed is False
    assert result.score == 0.0


async def test_budget_pass_when_equal_max() -> None:
    ctx = ScorerContext(row_metadata={EVAL_COST_USD_METADATA_KEY: 0.5})
    result = await CostBudgetScorer(max_cost_usd=0.5).score(STUB_REPLY, "", context=ctx)
    assert result.passed is True


def test_cost_budget_registered() -> None:
    assert EVAL_COST_SCORER_NAME in scorer_registry.available()
    scorer = scorer_registry.get(EVAL_COST_SCORER_NAME)({})
    assert scorer.name == EVAL_COST_SCORER_NAME


def test_factory_rejects_negative_rate() -> None:
    with pytest.raises(ConfigError, match="usd_per_1k_output_chars"):
        scorer_registry.get(EVAL_COST_SCORER_NAME)({"usd_per_1k_output_chars": -1})


def test_factory_rejects_negative_input_token_rate() -> None:
    with pytest.raises(ConfigError, match="usd_per_1k_input_tokens"):
        scorer_registry.get(EVAL_COST_SCORER_NAME)({"usd_per_1k_input_tokens": -1})


def test_factory_rejects_negative_output_token_rate() -> None:
    with pytest.raises(ConfigError, match="usd_per_1k_output_tokens"):
        scorer_registry.get(EVAL_COST_SCORER_NAME)({"usd_per_1k_output_tokens": -1})


def test_factory_rejects_bool_rate() -> None:
    with pytest.raises(ConfigError, match="must be a number"):
        scorer_registry.get(EVAL_COST_SCORER_NAME)({"usd_per_1k_input_tokens": True})


def test_factory_rejects_negative_max_cost() -> None:
    with pytest.raises(ConfigError, match="max_cost_usd"):
        scorer_registry.get(EVAL_COST_SCORER_NAME)({"max_cost_usd": -1})


def test_factory_rejects_bool_max_cost() -> None:
    with pytest.raises(ConfigError, match="must be a number"):
        scorer_registry.get(EVAL_COST_SCORER_NAME)({"max_cost_usd": True})


async def test_factory_null_max_cost_is_measure_only() -> None:
    scorer = scorer_registry.get(EVAL_COST_SCORER_NAME)({"max_cost_usd": None})
    result = await scorer.score(
        STUB_REPLY,
        "",
        context=ScorerContext(row_metadata={EVAL_COST_USD_METADATA_KEY: 99.0}),
    )
    assert result.passed is True
    assert result.metadata["max_cost_usd"] is None


async def test_factory_forwards_max_cost_usd() -> None:
    scorer = scorer_registry.get(EVAL_COST_SCORER_NAME)({"max_cost_usd": 0.5})
    over = await scorer.score(
        STUB_REPLY,
        "",
        context=ScorerContext(row_metadata={EVAL_COST_USD_METADATA_KEY: 1.0}),
    )
    under = await scorer.score(
        STUB_REPLY,
        "",
        context=ScorerContext(row_metadata={EVAL_COST_USD_METADATA_KEY: 0.4}),
    )
    assert over.passed is False
    assert under.passed is True
    assert over.metadata["max_cost_usd"] == 0.5


async def test_factory_forwards_custom_char_rate() -> None:
    rate = 2.0
    scorer = scorer_registry.get(EVAL_COST_SCORER_NAME)({"usd_per_1k_output_chars": rate})
    prediction = "abcd"
    result = await scorer.score(prediction, "")
    expected = len(prediction) / EVAL_COST_TOKENS_PER_THOUSAND * rate
    assert result.metadata[EVAL_COST_USD_METADATA_KEY] == pytest.approx(expected)
    assert result.metadata["source"] == EVAL_COST_SOURCE_OUTPUT_CHARS


def test_require_non_negative_float_rejects_negative() -> None:
    with pytest.raises(ConfigError, match=r"finite number >= 0.0"):
        require_non_negative_float(-0.01, owner=EVAL_COST_SCORER_NAME, field="max_cost_usd")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_require_non_negative_float_rejects_non_finite(value: float) -> None:
    with pytest.raises(ConfigError, match=r"finite number >= 0.0"):
        require_non_negative_float(value, owner=EVAL_COST_SCORER_NAME, field="max_cost_usd")


def test_constructor_rejects_negative_max_cost() -> None:
    with pytest.raises(ConfigError, match="max_cost_usd"):
        CostBudgetScorer(max_cost_usd=-1)


def test_constructor_rejects_non_finite_rate() -> None:
    with pytest.raises(ConfigError, match="usd_per_1k_output_chars"):
        CostBudgetScorer(usd_per_1k_output_chars=float("nan"))


def test_factory_rejects_non_finite_max_cost() -> None:
    with pytest.raises(ConfigError, match="max_cost_usd"):
        scorer_registry.get(EVAL_COST_SCORER_NAME)({"max_cost_usd": float("inf")})


def test_estimate_ignores_non_finite_explicit_cost() -> None:
    cost, source = estimate_cost_usd(
        STUB_REPLY,
        metadata={EVAL_COST_USD_METADATA_KEY: float("nan")},
        usd_per_1k_input_tokens=DEFAULT_EVAL_COST_USD_PER_1K_INPUT_TOKENS,
        usd_per_1k_output_tokens=DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_TOKENS,
        usd_per_1k_output_chars=DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_CHARS,
    )
    assert source == EVAL_COST_SOURCE_OUTPUT_CHARS
    assert cost == pytest.approx(_char_cost(STUB_REPLY))


def test_estimate_ignores_negative_token_metadata() -> None:
    cost, source = estimate_cost_usd(
        STUB_REPLY,
        metadata={EVAL_COST_INPUT_TOKENS_METADATA_KEY: -5},
        usd_per_1k_input_tokens=DEFAULT_EVAL_COST_USD_PER_1K_INPUT_TOKENS,
        usd_per_1k_output_tokens=DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_TOKENS,
        usd_per_1k_output_chars=DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_CHARS,
    )
    assert source == EVAL_COST_SOURCE_OUTPUT_CHARS
    assert cost == pytest.approx(_char_cost(STUB_REPLY))


def test_estimate_ignores_bool_explicit_cost() -> None:
    cost, source = estimate_cost_usd(
        STUB_REPLY,
        metadata={EVAL_COST_USD_METADATA_KEY: True},
        usd_per_1k_input_tokens=DEFAULT_EVAL_COST_USD_PER_1K_INPUT_TOKENS,
        usd_per_1k_output_tokens=DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_TOKENS,
        usd_per_1k_output_chars=DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_CHARS,
    )
    assert source == EVAL_COST_SOURCE_OUTPUT_CHARS
    assert cost == pytest.approx(_char_cost(STUB_REPLY))


_hypothesis = pytest.importorskip("hypothesis")
given = cast(Callable[..., Callable[..., Any]], _hypothesis.given)
settings = cast(Callable[..., Callable[..., Any]], _hypothesis.settings)
st: Any = pytest.importorskip("hypothesis.strategies")


@settings(max_examples=100)
@given(st.text())
def test_measure_only_is_non_negative_and_passing(prediction: str) -> None:
    scorer = CostBudgetScorer()
    result = asyncio.run(scorer.score(prediction, "ignored"))
    assert result.passed is True
    assert result.score == 1.0
    assert result.metadata[EVAL_COST_USD_METADATA_KEY] == pytest.approx(_char_cost(prediction))
    assert result.metadata["source"] == EVAL_COST_SOURCE_OUTPUT_CHARS


@settings(max_examples=50)
@given(st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False))
def test_explicit_cost_is_returned_unchanged(cost_usd: float) -> None:
    got, source = estimate_cost_usd(
        STUB_REPLY,
        metadata={EVAL_COST_USD_METADATA_KEY: cost_usd},
        usd_per_1k_input_tokens=DEFAULT_EVAL_COST_USD_PER_1K_INPUT_TOKENS,
        usd_per_1k_output_tokens=DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_TOKENS,
        usd_per_1k_output_chars=DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_CHARS,
    )
    assert source == EVAL_COST_SOURCE_EXPLICIT
    assert got == pytest.approx(cost_usd)
