"""Tests for the Scorer protocol and ScoreResult invariants."""

from __future__ import annotations

import pytest

from mangomas.eval.protocol import Scorer, ScorerContext, ScoreResult
from mangomas.eval.scorers.exact_match import ExactMatchScorer


def test_score_result_rejects_out_of_range_low() -> None:
    with pytest.raises(ValueError):
        ScoreResult(score=-0.1, passed=False)


def test_score_result_rejects_out_of_range_high() -> None:
    with pytest.raises(ValueError):
        ScoreResult(score=1.1, passed=True)


def test_score_result_accepts_boundaries() -> None:
    ScoreResult(score=0.0, passed=False)
    ScoreResult(score=1.0, passed=True)


def test_exact_match_scorer_satisfies_scorer_protocol() -> None:
    scorer = ExactMatchScorer()
    assert isinstance(scorer, Scorer)


def test_scorer_context_defaults() -> None:
    ctx = ScorerContext()
    assert ctx.llm is None
    assert ctx.row_metadata == {}
    assert ctx.correlation_id is None
