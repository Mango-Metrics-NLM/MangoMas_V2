"""Tests for the LLM-as-judge scorer."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from mangomas.errors import ConfigError, LLMBadResponse
from mangomas.eval.protocol import JudgeAware, ScorerContext
from mangomas.eval.registry import scorer_registry
from mangomas.eval.scorers.llm_judge import LLMJudgeScorer, judge_status_from_options
from mangomas.utils import clock
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
    # ConfigError (not ValueError): unified onto the shared eval option-error
    # taxonomy via `require_unit_float` (mangomas.eval._options).
    with pytest.raises(ConfigError):
        LLMJudgeScorer(threshold=1.5)


def test_llm_judge_registered_in_registry() -> None:
    assert "llm_judge" in scorer_registry.available()
    scorer = scorer_registry.get("llm_judge")({"threshold": 0.8})
    assert scorer.name == "llm_judge"
    assert isinstance(scorer, LLMJudgeScorer)


@pytest.mark.parametrize("raw", ['{"score": NaN}', '{"score": Infinity}', '{"score": "nan"}'])
async def test_llm_judge_non_finite_score_raises_bad_response(raw: str) -> None:
    """``json.loads`` accepts ``NaN``/``Infinity`` and ``float("nan")`` accepts the
    string form; ``min(1.0, nan)`` is 1.0, so an unguarded NaN scored a perfect
    pass. Mutation: delete the ``isfinite`` guard -> ``passed is True``."""
    scorer = LLMJudgeScorer(threshold=0.7)
    with pytest.raises(LLMBadResponse, match="not finite"):
        await scorer.score("p", "e", context=ScorerContext(llm=FakeLLM(reply=raw)))


def test_llm_judge_factory_rejects_non_numeric_threshold() -> None:
    """A mistyped option is a config error (exit 2), not a bare ``ValueError``."""
    with pytest.raises(ConfigError, match="threshold"):
        scorer_registry.get("llm_judge")({"threshold": "high"})


async def test_judge_warns_once_that_it_grades_with_the_system_under_test_client(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """spec-0045 R7: self-grading is reported, so a reader never mistakes it for calibration."""
    scorer = LLMJudgeScorer()
    ctx = ScorerContext(llm=FakeLLM(reply='{"score": 0.9, "rationale": "fine"}'))
    with caplog.at_level(logging.WARNING, logger="mangomas.eval.scorers.llm_judge"):
        await scorer.score("a", "b", context=ctx)
        await scorer.score("a", "b", context=ctx)
    warnings = [
        r for r in caplog.records if r.__dict__.get("event") == "eval_llm_judge_self_grading"
    ]
    assert len(warnings) == 1


# --- calibration status (spec-0047) ------------------------------------------


def _write_record(path: Path, **overrides: object) -> Path:
    record: dict[str, object] = {
        "judge_model": "judge-model",
        "measured_at": clock.now().date().isoformat(),
        "sample_size": 300,
        "cohens_kappa": 0.9,
        "self_preference": "not_measured",
    }
    record.update(overrides)
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def test_a_bare_judge_is_uncalibrated_and_says_so() -> None:
    scorer = LLMJudgeScorer()
    assert isinstance(scorer, JudgeAware)
    assert scorer.judge_status.calibrated is False
    assert scorer.judge_status.reason == "no calibration record"


def test_factory_builds_the_status_from_options(tmp_path: Path) -> None:
    path = _write_record(tmp_path / "cal.json")
    calibrated = scorer_registry.get("llm_judge")(
        {"calibration_path": str(path), "judge_model": "judge-model"}
    )
    assert isinstance(calibrated, LLMJudgeScorer) and calibrated.judge_status.calibrated is True
    other_model = scorer_registry.get("llm_judge")(
        {"calibration_path": str(path), "judge_model": "another-model"}
    )
    assert isinstance(other_model, LLMJudgeScorer)
    assert other_model.judge_status.calibrated is False
    assert "record is for model" in other_model.judge_status.reason
    stricter = scorer_registry.get("llm_judge")(
        {"calibration_path": str(path), "judge_model": "judge-model", "min_sample_size": 500}
    )
    assert isinstance(stricter, LLMJudgeScorer) and stricter.judge_status.calibrated is False
    no_model = scorer_registry.get("llm_judge")({"calibration_path": str(path)})
    assert isinstance(no_model, LLMJudgeScorer)
    assert no_model.judge_status.reason == "judge model unknown"


@pytest.mark.parametrize(
    "options",
    [
        {"calibration_path": 7},
        {"judge_model": 7},
        {"min_sample_size": 0},
        {"min_sample_size": True},
        {"max_age_days": "soon"},
        {"min_kappa": 1.5},
        {"min_rho": "high"},
        {"calibration_path": "/nonexistent/record.json", "judge_model": "judge-model"},
    ],
)
def test_factory_rejects_bad_calibration_options(options: dict[str, object]) -> None:
    with pytest.raises(ConfigError):
        judge_status_from_options(options)


async def test_the_self_grading_warning_carries_the_calibration_status(
    caplog: pytest.LogCaptureFixture,
) -> None:
    scorer = LLMJudgeScorer()
    ctx = ScorerContext(llm=FakeLLM(reply='{"score": 0.9, "rationale": "fine"}'))
    with caplog.at_level(logging.WARNING, logger="mangomas.eval.scorers.llm_judge"):
        await scorer.score("a", "b", context=ctx)
    record = next(
        r for r in caplog.records if r.__dict__.get("event") == "eval_llm_judge_self_grading"
    )
    assert record.__dict__["judge_calibrated"] is False
    assert "uncalibrated" in record.getMessage()
