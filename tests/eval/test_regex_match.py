"""Tests for the regex_match scorer."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any, cast

import pytest

import mangomas.eval.scorers  # noqa: F401 — registers scorers
from mangomas.errors import ConfigError
from mangomas.eval import scorer_registry
from mangomas.eval.scorers.regex_match import RegexMatchScorer


async def test_regex_match_searches_substring() -> None:
    result = await RegexMatchScorer().score("the answer is 42", r"\d+")
    assert result.passed is True
    assert result.score == 1.0
    assert result.metadata["matched_span"] is not None


async def test_regex_match_no_match() -> None:
    result = await RegexMatchScorer().score("no digits here", r"\d+")
    assert result.passed is False
    assert result.metadata["matched_span"] is None


async def test_regex_match_ignorecase_flag() -> None:
    result = await RegexMatchScorer(flags=["ignorecase"]).score("HELLO", r"hello")
    assert result.passed is True


async def test_regex_match_fullmatch_requires_whole_string() -> None:
    scorer = RegexMatchScorer(fullmatch=True)
    assert (await scorer.score("abc", r"abc")).passed is True
    assert (await scorer.score("abcd", r"abc")).passed is False


async def test_regex_match_invalid_pattern_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        await RegexMatchScorer().score("x", r"(unclosed")


def test_regex_match_unknown_flag_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        RegexMatchScorer(flags=["nope"])


def test_regex_match_registered() -> None:
    scorer = scorer_registry.get("regex_match")({"flags": ["ignorecase"], "fullmatch": True})
    assert scorer.name == "regex_match"


def test_regex_match_factory_ignores_non_list_flags() -> None:
    scorer = scorer_registry.get("regex_match")({"flags": "ignorecase"})
    assert scorer.name == "regex_match"


# ── Hypothesis fuzz ───────────────────────────────────────────────────────────
_hypothesis = pytest.importorskip("hypothesis")
given = cast(Callable[..., Callable[..., Any]], _hypothesis.given)
settings = cast(Callable[..., Callable[..., Any]], _hypothesis.settings)
st: Any = pytest.importorskip("hypothesis.strategies")


@settings(max_examples=150)
@given(st.text(min_size=1))
def test_regex_match_escaped_literal_always_self_matches(text: str) -> None:
    scorer = RegexMatchScorer()
    result = asyncio.run(scorer.score(text, re.escape(text)))
    assert result.passed is True


@settings(max_examples=150)
@given(st.text(), st.text(min_size=1))
def test_regex_match_never_raises_on_literal_pattern(prediction: str, needle: str) -> None:
    scorer = RegexMatchScorer()
    # Escaped needle is always a valid pattern → must not raise.
    result = asyncio.run(scorer.score(prediction, re.escape(needle)))
    assert 0.0 <= result.score <= 1.0
