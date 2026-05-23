"""Tests for the LLM-as-judge scorer."""

from __future__ import annotations

import pytest

from mangomas.errors import LLMBadResponse
from mangomas.eval.protocol import ScorerContext
from mangomas.eval.registry import scorer_registry
from mangomas.eval.scorers.llm_judge import LLMJudgeScorer
from tests.fakes import FakeLLM


def _ctx(reply: str) -> ScorerContext:
    """Build a ``ScorerContext`` whose LLM returns ``reply`` once."""
    return ScorerContext(llm=FakeLLM(reply=reply))


async def test_llm_judge_returns_clamped_score() -> None:
    scorer = LLMJudgeScorer(threshold=0.7)
    result = await scorer.score(
        "answer",
        "expected",
        context=_ctx('{"score": 0.85, "rationale": "close enough"}'),
    )
    assert result.score == pytest.approx(0.85)
    assert result.passed is True
    assert result.metadata["rationale"] == "close enough"
    assert result.metadata["scorer"] == "llm_judge"


async def test_llm_judge_below_threshold_fails() -> None:
    scorer = LLMJudgeScorer(threshold=0.9)
    result = await scorer.score(
        "answer",
        "expected",
        context=_ctx('{"score": 0.5, "rationale": "off"}'),
    )
    assert result.passed is False


async def test_llm_judge_clamps_out_of_range_score() -> None:
    scorer = LLMJudgeScorer()
    result = await scorer.score(
        "answer",
        "expected",
        context=_ctx('{"score": 1.5, "rationale": "over"}'),
    )
    assert result.score == 1.0
    assert result.metadata["raw_score"] == 1.5


async def test_llm_judge_missing_score_raises_bad_response() -> None:
    scorer = LLMJudgeScorer()
    with pytest.raises(LLMBadResponse):
        await scorer.score(
            "a",
            "b",
            context=_ctx('{"rationale": "no score field"}'),
        )


async def test_llm_judge_non_numeric_score_raises_bad_response() -> None:
    scorer = LLMJudgeScorer()
    with pytest.raises(LLMBadResponse):
        await scorer.score(
            "a",
            "b",
            context=_ctx('{"score": "high"}'),
        )


async def test_llm_judge_invalid_json_raises_bad_response() -> None:
    scorer = LLMJudgeScorer()
    with pytest.raises(LLMBadResponse):
        await scorer.score(
            "a",
            "b",
            context=_ctx("not json at all"),
        )


async def test_llm_judge_without_context_raises_bad_response() -> None:
    scorer = LLMJudgeScorer()
    with pytest.raises(LLMBadResponse):
        await scorer.score("a", "b", context=None)


async def test_llm_judge_without_llm_raises_bad_response() -> None:
    scorer = LLMJudgeScorer()
    with pytest.raises(LLMBadResponse):
        await scorer.score("a", "b", context=ScorerContext(llm=None))


def test_llm_judge_rejects_invalid_threshold() -> None:
    with pytest.raises(ValueError):
        LLMJudgeScorer(threshold=1.5)


def test_llm_judge_registered_in_registry() -> None:
    assert "llm_judge" in scorer_registry.available()
    scorer = scorer_registry.get("llm_judge")({"threshold": 0.8})
    assert scorer.name == "llm_judge"
    assert isinstance(scorer, LLMJudgeScorer)
