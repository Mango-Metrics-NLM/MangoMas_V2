"""pass^k trials (spec-0046 / reliability plan C1).

``MANGOMAS_EVAL__TRIALS=k`` repeats every row ``k`` times; ``collapse_trials``
folds the attempts into one row that passes only when all of them did, so the
report's pass rate *is* pass^k. The headline test is the plan's named one: a
single failing attempt out of ``k`` takes the row — and ``pass_pow_k`` — down,
while ``trial_pass_rate`` still shows the per-attempt view.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, cast

import pytest

from mangomas.agents import ChatAgent
from mangomas.config import EvalSettings
from mangomas.core import AgentContext, Message, Orchestrator
from mangomas.eval import EvalRunner, load_baseline
from mangomas.eval.dataset import DatasetRow
from mangomas.eval.protocol import ScorerContext, ScoreResult
from mangomas.eval.runner import (
    _FAIL_FAST_ERROR,
    EVAL_TRIALS_METADATA_KEY,
    EvalRowResult,
    collapse_trials,
)
from mangomas.eval.scorers.exact_match import ExactMatchScorer
from mangomas.eval.sinks.json_file import JsonFileSink
from mangomas.eval.targets import AgentTarget
from tests.constants import (
    DEFAULT_AGENT_NAME,
    DEFAULT_EVAL_JUDGE_CALIBRATION_MAX_AGE_DAYS,
    DEFAULT_EVAL_JUDGE_MIN_KAPPA,
    DEFAULT_EVAL_JUDGE_MIN_RHO,
    DEFAULT_EVAL_JUDGE_MIN_SAMPLE_SIZE,
    DEFAULT_EVAL_TRIALS,
    EVAL_COST_USD_METADATA_KEY,
    STUB_REPLY,
)
from tests.fakes import FakeLLM, FakeRepository

_WRONG = "not-the-expected-answer"
_K = 3


def _detail(row: EvalRowResult) -> dict[str, Any]:
    return cast("dict[str, Any]", row.metadata[EVAL_TRIALS_METADATA_KEY])


def _row(row_id: str = "r1") -> DatasetRow:
    return DatasetRow(id=row_id, messages=[Message(role="user", content="hi")], expected=STUB_REPLY)


def _orchestrator(llm: FakeLLM) -> Orchestrator:
    orch = Orchestrator(AgentContext(llm=llm, repo=FakeRepository()))
    orch.register(ChatAgent())
    return orch


async def _run(llm: FakeLLM, rows: list[DatasetRow], **kwargs: object) -> tuple[object, FakeLLM]:
    runner = EvalRunner(_orchestrator(llm), ExactMatchScorer(), **kwargs)  # type: ignore[arg-type]
    return await runner.run(rows, target=AgentTarget(agent=DEFAULT_AGENT_NAME)), llm


async def test_pass_pow_k_falls_when_one_trial_of_k_fails() -> None:
    """The reliability plan's named acceptance: pass, fail, pass is not a pass."""
    report, llm = await _run(FakeLLM(replies=[STUB_REPLY, _WRONG, STUB_REPLY]), [_row()], trials=_K)
    assert len(llm.calls) == _K, "every attempt reached the model"
    row = report.rows[0]  # type: ignore[attr-defined]
    assert row.passed is False and row.error is None
    assert row.score == pytest.approx(2 / 3)
    detail = _detail(row)
    assert (detail["count"], detail["passed"], detail["errored"]) == (_K, 2, 0)
    assert detail["scores"] == [1.0, 0.0, 1.0]
    assert len(set(detail["correlation_ids"])) == _K, "each attempt has its own correlation id"
    assert row.correlation_id == detail["correlation_ids"][0]
    assert (report.trials, report.pass_pow_k) == (_K, 0.0)  # type: ignore[attr-defined]
    assert report.trial_pass_rate == pytest.approx(2 / 3)  # type: ignore[attr-defined]
    assert (report.passed, report.failed, report.errored) == (0, 1, 0)  # type: ignore[attr-defined]


async def test_all_trials_passing_passes_the_row() -> None:
    report, _ = await _run(FakeLLM(), [_row(), _row("r2")], trials=_K)
    assert all(r.passed for r in report.rows)  # type: ignore[attr-defined]
    assert report.pass_pow_k == 1.0 and report.trial_pass_rate == 1.0  # type: ignore[attr-defined]
    assert _detail(report.rows[0])["passed"] == _K  # type: ignore[attr-defined]


async def test_single_trial_is_the_attempt_itself() -> None:
    """``k=1`` is byte-identical: no trials block, and the three fields restate the pass rate."""
    assert DEFAULT_EVAL_TRIALS == 1
    report, llm = await _run(FakeLLM(replies=[STUB_REPLY, _WRONG]), [_row(), _row("r2")])
    assert len(llm.calls) == 2
    assert all(EVAL_TRIALS_METADATA_KEY not in r.metadata for r in report.rows)  # type: ignore[attr-defined]
    assert report.trials == 1  # type: ignore[attr-defined]
    assert report.pass_pow_k == report.trial_pass_rate == 0.5  # type: ignore[attr-defined]
    attempt = EvalRowResult(
        row_id="x", score=1.0, passed=True, duration_ms=1.0, prediction="p", expected="p"
    )
    assert collapse_trials([attempt]) is attempt


class _ErrorsOnSecondCall:
    name = "flaky_scorer"

    def __init__(self) -> None:
        self.calls = 0

    async def score(
        self,
        prediction: str,  # noqa: ARG002 — fixed verdict
        expected: str,  # noqa: ARG002 — fixed verdict
        *,
        context: ScorerContext | None = None,  # noqa: ARG002 — fixed verdict
    ) -> ScoreResult:
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("judge offline")
        return ScoreResult(score=1.0, passed=True, metadata={EVAL_COST_USD_METADATA_KEY: 0.2})


async def test_an_erroring_attempt_marks_the_row_errored_once() -> None:
    runner = EvalRunner(_orchestrator(FakeLLM()), _ErrorsOnSecondCall(), trials=_K)
    report = await runner.run([_row()], target=AgentTarget(agent=DEFAULT_AGENT_NAME))
    row = report.rows[0]
    assert row.passed is False and row.error is not None and "judge offline" in row.error
    assert _detail(row)["errored"] == 1
    assert row.score == 1.0, "mean over the non-errored attempts"
    assert (report.passed, report.failed, report.errored) == (0, 0, 1)
    assert report.trial_pass_rate == pytest.approx(2 / 3)
    assert report.mean_cost_usd is None, "an errored row never contributes cost"


def _attempt(
    score: float, *, passed: bool, cost: float | None = None, error: str | None = None
) -> EvalRowResult:
    metadata: dict[str, object] = {"scorer": "x"}
    if cost is not None:
        metadata[EVAL_COST_USD_METADATA_KEY] = cost
    return EvalRowResult(
        row_id="r1",
        score=score,
        passed=passed,
        duration_ms=10.0,
        prediction=f"p{score}",
        expected="e",
        metadata=metadata,
        error=error,
        correlation_id=f"c{score}",
        response_metadata={"llm_profile": "x"},
    )


def test_collapse_averages_cost_sums_duration_and_keeps_the_first_attempt() -> None:
    row = collapse_trials(
        [_attempt(1.0, passed=True, cost=0.1), _attempt(0.5, passed=False, cost=0.3)]
    )
    assert (row.passed, row.score, row.duration_ms) == (False, 0.75, 20.0)
    assert row.metadata[EVAL_COST_USD_METADATA_KEY] == pytest.approx(0.2)
    assert (row.prediction, row.correlation_id, row.response_metadata) == (
        "p1.0",
        "c1.0",
        {"llm_profile": "x"},
    )
    assert row.metadata["scorer"] == "x"
    assert row.error is None


def test_collapse_with_every_attempt_errored_scores_zero_and_keeps_the_first_error() -> None:
    row = collapse_trials(
        [_attempt(0.0, passed=False, error="boom-1"), _attempt(0.0, passed=False, error="boom-2")]
    )
    assert (row.score, row.passed, row.error) == (0.0, False, "boom-1")
    assert EVAL_COST_USD_METADATA_KEY not in row.metadata
    assert _detail(row)["errored"] == 2


def test_collapse_requires_at_least_one_attempt() -> None:
    with pytest.raises(ValueError, match="at least one"):
        collapse_trials([])


def test_runner_rejects_invalid_trials() -> None:
    with pytest.raises(ValueError, match="trials"):
        EvalRunner(_orchestrator(FakeLLM()), ExactMatchScorer(), trials=0)


async def test_concurrent_dispatch_collapses_trials_too() -> None:
    report, llm = await _run(FakeLLM(), [_row(), _row("r2")], trials=2, parallelism=2)
    assert len(llm.calls) == 4
    assert report.trials == 2 and report.pass_pow_k == 1.0  # type: ignore[attr-defined]
    assert all(_detail(r)["count"] == 2 for r in report.rows)  # type: ignore[attr-defined]


async def test_fail_fast_with_trials_skips_rows_without_crashing() -> None:
    """Copilot review on PR #125: a fail-fast placeholder carries no trials block.

    Row r1 attempts pass then fail, so fail_fast trips and r2 / r3 are never
    dispatched. The run must still produce a report; the per-attempt rate is
    over the attempts actually made (1 of 2), not over rows times k.
    """
    report, llm = await _run(
        FakeLLM(replies=[STUB_REPLY, _WRONG]),
        [_row(), _row("r2"), _row("r3")],
        trials=2,
        fail_fast=True,
    )
    assert len(llm.calls) == 2, "only r1's two attempts reached the model"
    skipped = [r for r in report.rows if r.error == _FAIL_FAST_ERROR]  # type: ignore[attr-defined]
    assert [r.row_id for r in skipped] == ["r2", "r3"]
    assert all(EVAL_TRIALS_METADATA_KEY not in r.metadata for r in skipped)
    assert (report.passed, report.failed, report.errored) == (0, 1, 2)  # type: ignore[attr-defined]
    assert (report.trials, report.pass_pow_k) == (2, 0.0)  # type: ignore[attr-defined]
    assert report.trial_pass_rate == 0.5  # type: ignore[attr-defined]


async def test_empty_dataset_reports_zero_pass_pow_k() -> None:
    report, _ = await _run(FakeLLM(), [], trials=_K)
    assert (report.trials, report.pass_pow_k, report.trial_pass_rate) == (_K, 0.0, 0.0)  # type: ignore[attr-defined]


async def test_trials_round_trip_through_a_json_baseline(tmp_path: Path) -> None:
    report, _ = await _run(FakeLLM(replies=[STUB_REPLY, _WRONG, STUB_REPLY]), [_row()], trials=_K)
    path = tmp_path / "report.json"
    await JsonFileSink(path=str(path)).emit(report)  # type: ignore[arg-type]
    loaded = await load_baseline(path)
    assert (loaded.trials, loaded.pass_pow_k) == (_K, 0.0)
    assert loaded.trial_pass_rate == pytest.approx(2 / 3)
    # A baseline written before spec-0046 has none of the three keys.
    legacy = {
        k: v
        for k, v in json.loads(path.read_text(encoding="utf-8")).items()
        if k not in {"trials", "pass_pow_k", "trial_pass_rate"}
    }
    path.write_text(json.dumps(legacy), encoding="utf-8")
    old = await load_baseline(path)
    assert (old.trials, old.pass_pow_k, old.trial_pass_rate) == (DEFAULT_EVAL_TRIALS, None, None)
    assert dataclasses.replace(old, trials=_K).trials == _K


def test_settings_validate_trials_and_the_reliability_threshold() -> None:
    assert EvalSettings().trials == DEFAULT_EVAL_TRIALS and EvalSettings().min_pass_pow_k is None
    assert EvalSettings(trials=4, min_pass_pow_k=0.9).min_pass_pow_k == 0.9
    with pytest.raises(ValueError, match="trials"):
        EvalSettings(trials=0)
    with pytest.raises(ValueError, match="min_pass_pow_k"):
        EvalSettings(min_pass_pow_k=1.5)


def test_settings_validate_the_judge_calibration_policy() -> None:
    defaults = EvalSettings()
    assert defaults.judge_calibration_path is None
    assert (defaults.judge_min_kappa, defaults.judge_min_rho) == (
        DEFAULT_EVAL_JUDGE_MIN_KAPPA,
        DEFAULT_EVAL_JUDGE_MIN_RHO,
    )
    assert defaults.judge_min_sample_size == DEFAULT_EVAL_JUDGE_MIN_SAMPLE_SIZE
    assert defaults.judge_calibration_max_age_days == DEFAULT_EVAL_JUDGE_CALIBRATION_MAX_AGE_DAYS
    for bad in (
        {"judge_min_kappa": 1.5},
        {"judge_min_rho": -0.1},
        {"judge_min_sample_size": 0},
        {"judge_calibration_max_age_days": 0},
    ):
        with pytest.raises(ValueError, match=next(iter(bad))):
            EvalSettings(**bad)


async def test_the_report_carries_the_judge_status_and_round_trips(tmp_path: Path) -> None:
    """spec-0047: a judge run stamps ``EvalReport.judge``; deterministic runs leave it None."""
    from mangomas.eval.scorers.llm_judge import LLMJudgeScorer  # noqa: PLC0415 — scoped

    judge_llm = FakeLLM(reply=json.dumps({"score": 0.9, "rationale": "ok"}))
    runner = EvalRunner(_orchestrator(judge_llm), LLMJudgeScorer())
    report = await runner.run([_row()], target=AgentTarget(agent=DEFAULT_AGENT_NAME))
    assert report.judge is not None and report.judge.calibrated is False
    path = tmp_path / "judge-report.json"
    await JsonFileSink(path=str(path)).emit(report)
    loaded = await load_baseline(path)
    assert loaded.judge == report.judge
    plain, _ = await _run(FakeLLM(), [_row()])
    assert plain.judge is None  # type: ignore[attr-defined]
    empty = await runner.run([], target=AgentTarget(agent=DEFAULT_AGENT_NAME))
    assert empty.judge is not None
