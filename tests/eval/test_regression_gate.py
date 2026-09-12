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


def test_merge_result_kinds() -> None:
    """evaluate_gate produces a threshold-kind verdict; evaluate_regression_gate
    produces a regression-kind verdict — the discriminator merge_gate_results
    uses instead of positional indexing (D11)."""
    report = EvalReport(
        scorer="s",
        agent_name="a",
        dataset_size=1,
        passed=1,
        failed=0,
        errored=0,
        mean_score=1.0,
        duration_ms=1.0,
        rows=[],
        target_name="a",
    )
    assert evaluate_gate(report).kind == "threshold"
    assert evaluate_regression_gate(_diff()).kind == "regression"


def test_merge_sources_threshold_fields_regardless_of_order() -> None:
    """D11 regression: merge_gate_results must take the threshold-gate's
    fields (min_mean_score, min_pass_rate, fail_on_error, errored) from the
    threshold-kind verdict specifically — not positionally from results[0].
    Previously the merge was only correct because the CLI always passed
    [threshold, regression] in that fixed order; this proves both orders
    produce the identical merged verdict.
    """
    report = EvalReport(
        scorer="s",
        agent_name="a",
        dataset_size=2,
        passed=1,
        failed=1,
        errored=1,
        mean_score=0.4,
        duration_ms=1.0,
        rows=[],
        target_name="a",
        mean_cost_usd=0.25,
    )
    threshold = evaluate_gate(
        report,
        min_mean_score=0.9,
        min_pass_rate=0.9,
        fail_on_error=True,
        max_mean_cost_usd=0.5,
    )
    regression = evaluate_regression_gate(_diff(mean_score_delta=-0.5), max_mean_score_drop=0.1)

    forward = merge_gate_results([threshold, regression])
    backward = merge_gate_results([regression, threshold])

    assert forward is not None
    assert backward is not None
    for field_name in (
        "passed",
        "min_mean_score",
        "min_pass_rate",
        "fail_on_error",
        "errored",
        "max_mean_cost_usd",
        "actual_mean_cost_usd",
    ):
        assert getattr(forward, field_name) == getattr(backward, field_name), field_name
    # Both orders must carry the *threshold* gate's real configured values —
    # not the regression gate's defaults (None/None/False/0) that a
    # positional results[0] read would have picked up in the backward order.
    assert backward.min_mean_score == 0.9
    assert backward.min_pass_rate == 0.9
    assert backward.fail_on_error is True
    assert backward.errored == 1
    assert backward.max_mean_cost_usd == 0.5
    assert backward.actual_mean_cost_usd == 0.25


def test_merge_falls_back_to_defaults_with_no_threshold_verdict() -> None:
    """Merging two regression-kind verdicts (no threshold gate engaged) keeps
    GateResult's own field defaults for the threshold-only fields."""
    a = evaluate_regression_gate(_diff())
    b = evaluate_regression_gate(_diff(mean_score_delta=-0.5), max_mean_score_drop=0.1)
    merged = merge_gate_results([a, b])
    assert merged is not None
    assert merged.min_mean_score is None
    assert merged.min_pass_rate is None
    assert merged.fail_on_error is False
    assert merged.errored == 0
