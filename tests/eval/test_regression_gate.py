"""Tests for evaluate_regression_gate + merge_gate_results."""

from __future__ import annotations

import dataclasses

from mangomas.eval import evaluate_gate, evaluate_regression_gate, merge_gate_results
from mangomas.eval.baseline import ReportDiff
from mangomas.eval.runner import EvalReport

_DEFAULT_DIFF = ReportDiff(
    baseline_mean_score=1.0,
    current_mean_score=1.0,
    mean_score_delta=0.0,
    baseline_pass_rate=1.0,
    current_pass_rate=1.0,
    pass_rate_delta=0.0,
    passed_delta=0,
    failed_delta=0,
    errored_delta=0,
)


def _diff(
    *,
    mean_score_delta: float = 0.0,
    pass_rate_delta: float = 0.0,
    regressed_rows: list[str] | None = None,
) -> ReportDiff:
    return dataclasses.replace(
        _DEFAULT_DIFF,
        mean_score_delta=mean_score_delta,
        pass_rate_delta=pass_rate_delta,
        regressed_rows=regressed_rows if regressed_rows is not None else [],
    )


def test_regression_gate_noop_passes() -> None:
    assert evaluate_regression_gate(_diff()).passed


def test_regression_gate_mean_drop_fails() -> None:
    result = evaluate_regression_gate(_diff(mean_score_delta=-0.2), max_mean_score_drop=0.1)
    assert not result.passed
    assert "mean_score dropped" in result.reasons[0]


def test_regression_gate_mean_drop_within_tolerance_passes() -> None:
    assert evaluate_regression_gate(_diff(mean_score_delta=-0.05), max_mean_score_drop=0.1).passed


def test_regression_gate_pass_rate_drop_fails() -> None:
    assert not evaluate_regression_gate(_diff(pass_rate_delta=-0.3), max_pass_rate_drop=0.1).passed


def test_regression_gate_new_failures_fail_when_disallowed() -> None:
    result = evaluate_regression_gate(_diff(regressed_rows=["r2", "r3"]), allow_new_failures=False)
    assert not result.passed
    assert "regression" in result.reasons[0]


def test_regression_gate_allows_new_failures_by_default() -> None:
    assert evaluate_regression_gate(_diff(regressed_rows=["r2"])).passed


def test_merge_none_returns_none() -> None:
    assert merge_gate_results([None, None]) is None


def test_merge_single_returns_same_object() -> None:
    gate = evaluate_regression_gate(_diff())
    assert merge_gate_results([gate, None]) is gate


def test_merge_both_ands_passed_and_concats_reasons() -> None:
    report = EvalReport(
        scorer="s",
        agent_name="a",
        dataset_size=1,
        passed=0,
        failed=1,
        errored=0,
        mean_score=0.0,
        duration_ms=1.0,
        rows=[],
        target_name="a",
    )
    threshold = evaluate_gate(report, min_mean_score=0.5)  # fails
    regression = evaluate_regression_gate(_diff(mean_score_delta=-0.5), max_mean_score_drop=0.1)
    merged = merge_gate_results([threshold, regression])
    assert merged is not None
    assert not merged.passed
    assert len(merged.reasons) == len(threshold.reasons) + len(regression.reasons)
