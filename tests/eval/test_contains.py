"""Tests for the contains scorer."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, cast

import pytest

import mangomas.eval.scorers  # noqa: F401 — registers scorers
from mangomas.eval import scorer_registry
from mangomas.eval.scorers.contains import ContainsScorer


async def test_contains_matches_substring() -> None:
    result = await ContainsScorer().score("the quick brown fox", "quick")
    assert result.passed is True
    assert result.score == 1.0


async def test_contains_case_insensitive_by_default() -> None:
    result = await ContainsScorer().score("Hello World", "hello")
    assert result.passed is True


async def test_contains_case_sensitive_option() -> None:
    result = await ContainsScorer(case_sensitive=True).score("Hello World", "hello")
    assert result.passed is False


async def test_contains_absent_substring() -> None:
    result = await ContainsScorer().score("abc", "xyz")
    assert result.passed is False
    assert result.score == 0.0


def test_contains_registered() -> None:
    scorer = scorer_registry.get("contains")({"case_sensitive": True})
    assert scorer.name == "contains"


# ── Hypothesis fuzz ───────────────────────────────────────────────────────────
_hypothesis = pytest.importorskip("hypothesis")
given = cast(Callable[..., Callable[..., Any]], _hypothesis.given)
settings = cast(Callable[..., Callable[..., Any]], _hypothesis.settings)
st: Any = pytest.importorskip("hypothesis.strategies")


@settings(max_examples=200)
@given(st.text(), st.text(min_size=1), st.text())
def test_contains_concatenation_always_contains_middle(a: str, b: str, c: str) -> None:
    scorer = ContainsScorer(case_sensitive=True)
    result = asyncio.run(scorer.score(a + b + c, b))
    assert result.passed is True
