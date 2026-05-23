"""Tests for the exact-match scorer."""

from __future__ import annotations

from mangomas.eval.registry import scorer_registry
from mangomas.eval.scorers.exact_match import ExactMatchScorer


async def test_exact_match_exact_strings_pass() -> None:
    scorer = ExactMatchScorer()
    result = await scorer.score("hello", "hello")
    assert result.score == 1.0
    assert result.passed is True


async def test_exact_match_different_strings_fail() -> None:
    scorer = ExactMatchScorer()
    result = await scorer.score("hello", "world")
    assert result.score == 0.0
    assert result.passed is False


async def test_exact_match_case_insensitive_by_default() -> None:
    scorer = ExactMatchScorer()
    result = await scorer.score("Hello", "HELLO")
    assert result.passed is True


async def test_exact_match_case_sensitive_when_configured() -> None:
    scorer = ExactMatchScorer(case_sensitive=True)
    result = await scorer.score("Hello", "HELLO")
    assert result.passed is False


async def test_exact_match_whitespace_normalised_by_default() -> None:
    scorer = ExactMatchScorer()
    result = await scorer.score("  hello   world  ", "hello world")
    assert result.passed is True


async def test_exact_match_whitespace_preserved_when_disabled() -> None:
    scorer = ExactMatchScorer(strip_whitespace=False)
    result = await scorer.score("  hello   world  ", "hello world")
    assert result.passed is False


def test_exact_match_registered_in_registry() -> None:
    assert "exact_match" in scorer_registry.available()
    scorer = scorer_registry.get("exact_match")({})
    assert scorer.name == "exact_match"


def test_exact_match_factory_forwards_options() -> None:
    scorer = scorer_registry.get("exact_match")({"case_sensitive": True, "strip_whitespace": False})
    assert isinstance(scorer, ExactMatchScorer)
    # Confirm the options stuck (private fields; SLF is allowed in tests).
    assert scorer._case_sensitive is True  # noqa: SLF001
    assert scorer._strip_whitespace is False  # noqa: SLF001
