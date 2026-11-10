"""Tests for the eval quality gate (``mangomas.eval.gate``)."""

from __future__ import annotations

import dataclasses
import logging
import math

import pytest

from mangomas.eval import GateResult, evaluate_gate, merge_gate_results
from mangomas.eval.gate import MIN_TRIALS_FOR_PASS_POW_K
from mangomas.eval.runner import EvalReport


def _report(
    *,
    dataset_size: int,
    passed: int,
    failed: int = 0,
    errored: int = 0,
    mean_score: float = 1.0,
    mean_cost_usd: float | None = None,
) -> EvalReport:
    return EvalReport(
        scorer="exact_match",
        agent_name="chat",
        dataset_size=dataset_size,
        passed=passed,
        failed=failed,
        errored=errored,
        mean_score=mean_score,
        duration_ms=1.0,
        rows=[],
        mean_cost_usd=mean_cost_usd,
    )


def test_gate_noop_passes_when_unconfigured() -> None:
    result = evaluate_gate(_report(dataset_size=2, passed=1, failed=1, mean_score=0.5))
    assert result.passed is True
    assert result.reasons == []


def test_gate_fails_below_min_mean_score() -> None:
    result = evaluate_gate(
        _report(dataset_size=2, passed=1, failed=1, mean_score=0.5),
        min_mean_score=0.9,
    )
    assert result.passed is False
    assert any("mean_score" in r for r in result.reasons)


def test_gate_fails_below_min_pass_rate() -> None:
    result = evaluate_gate(
        _report(dataset_size=4, passed=1, failed=3, mean_score=1.0),
        min_pass_rate=0.75,
    )
    assert result.passed is False
    assert result.actual_pass_rate == 0.25
    assert any("pass_rate" in r for r in result.reasons)


def test_gate_boundary_equal_threshold_passes() -> None:
    result = evaluate_gate(
        _report(dataset_size=4, passed=3, failed=1, mean_score=0.75),
        min_mean_score=0.75,
        min_pass_rate=0.75,
    )
    assert result.passed is True


def test_gate_fail_on_error_with_errored_rows() -> None:
    result = evaluate_gate(
        _report(dataset_size=3, passed=2, failed=0, errored=1, mean_score=1.0),
        fail_on_error=True,
    )
    assert result.passed is False
    assert any("errored" in r for r in result.reasons)


def test_gate_fail_on_error_clean_run_passes() -> None:
    result = evaluate_gate(
        _report(dataset_size=2, passed=2, mean_score=1.0),
        fail_on_error=True,
    )
    assert result.passed is True


def test_gate_empty_dataset_passes_with_note() -> None:
    result = evaluate_gate(
        _report(dataset_size=0, passed=0, mean_score=0.0),
        min_mean_score=0.9,
    )
    assert result.passed is True
    assert result.reasons == ["empty dataset — nothing to gate"]


def test_gate_result_is_frozen_dataclass() -> None:
    result = evaluate_gate(_report(dataset_size=1, passed=1))
    assert isinstance(result, GateResult)


def test_gate_fails_above_max_mean_cost_usd() -> None:
    result = evaluate_gate(
        _report(dataset_size=2, passed=2, mean_score=1.0, mean_cost_usd=1.0),
        max_mean_cost_usd=0.01,
    )
    assert result.passed is False
    assert any("mean_cost_usd" in reason for reason in result.reasons)


def test_gate_skips_cost_when_mean_unavailable() -> None:
    skipped = evaluate_gate(
        _report(dataset_size=2, passed=2, mean_score=1.0),
        max_mean_cost_usd=0.01,
    )
    assert skipped.passed is True
    assert any("unavailable" in reason for reason in skipped.reasons)


def test_gate_quality_fail_keeps_unavailable_cost_note() -> None:
    result = evaluate_gate(
        _report(dataset_size=2, passed=1, failed=1, mean_score=0.1),
        min_mean_score=0.9,
        max_mean_cost_usd=0.01,
    )
    assert result.passed is False
    assert any("mean_score" in reason for reason in result.reasons)
    assert any("unavailable" in reason for reason in result.reasons)


def test_gate_equal_max_mean_cost_usd_passes() -> None:
    result = evaluate_gate(
        _report(dataset_size=2, passed=2, mean_score=1.0, mean_cost_usd=0.05),
        max_mean_cost_usd=0.05,
    )
    assert result.passed is True
    assert result.actual_mean_cost_usd == 0.05


def test_gate_empty_dataset_skips_cost_threshold() -> None:
    result = evaluate_gate(
        _report(dataset_size=0, passed=0, mean_score=0.0),
        max_mean_cost_usd=0.01,
    )
    assert result.passed is True
    assert result.reasons == ["empty dataset — nothing to gate"]


def test_gate_cost_within_budget_passes() -> None:
    result = evaluate_gate(
        _report(dataset_size=2, passed=2, mean_score=1.0, mean_cost_usd=0.01),
        max_mean_cost_usd=0.05,
    )
    assert result.passed is True
    assert result.actual_mean_cost_usd == 0.01
    assert result.max_mean_cost_usd == 0.05


def test_gate_fails_non_finite_max_mean_cost_usd() -> None:
    result = evaluate_gate(
        _report(dataset_size=2, passed=2, mean_score=1.0, mean_cost_usd=0.01),
        max_mean_cost_usd=float("nan"),
    )
    assert result.passed is False
    assert "max_mean_cost_usd is not a finite number" in result.reasons


def test_gate_fails_non_finite_mean_cost_usd() -> None:
    result = evaluate_gate(
        _report(dataset_size=2, passed=2, mean_score=1.0, mean_cost_usd=float("inf")),
        max_mean_cost_usd=0.05,
    )
    assert result.passed is False
    assert "mean_cost_usd is not a finite number" in result.reasons


def test_gate_emits_structured_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="mangomas.eval.gate"):
        evaluate_gate(_report(dataset_size=2, passed=2), min_mean_score=0.5)
    events = [rec for rec in caplog.records if getattr(rec, "event", None) == "eval_gate"]
    assert events, "expected an eval_gate log record"


def test_gate_fails_non_finite_mean_score_when_threshold_set() -> None:
    """``nan < x`` is always False; without the guard a NaN mean passes every
    threshold. Mutation: remove the ``isfinite`` branch -> ``passed is True``."""
    result = evaluate_gate(
        _report(dataset_size=2, passed=1, mean_score=float("nan")), min_mean_score=0.5
    )
    assert result.passed is False
    assert "mean_score is not a finite number" in result.reasons


def test_gate_ignores_non_finite_mean_score_without_threshold() -> None:
    """No threshold means the mean is never inspected — the no-op gate passes."""
    result = evaluate_gate(_report(dataset_size=2, passed=2, mean_score=float("nan")))
    assert result.passed is True
    assert result.reasons == []


# --- min_pass_pow_k (spec-0046) ---------------------------------------------
#
# The reliability threshold fails *closed*: a single attempt cannot measure
# pass^k, so a k=1 run fails the threshold rather than skipping it (the cost
# gate's skip rule is deliberately not reused).


def _trials_report(*, trials: int, pass_pow_k: float | None, dataset_size: int = 2) -> EvalReport:
    measured = pass_pow_k if pass_pow_k is not None and math.isfinite(pass_pow_k) else 0.0
    passed = round(measured * dataset_size)
    return dataclasses.replace(
        _report(dataset_size=dataset_size, passed=passed, failed=dataset_size - passed),
        trials=trials,
        pass_pow_k=pass_pow_k,
    )


def test_gate_min_pass_pow_k_fails_closed_on_a_single_trial() -> None:
    result = evaluate_gate(_trials_report(trials=1, pass_pow_k=1.0), min_pass_pow_k=0.5)
    assert result.passed is False, "a perfect single-trial run still cannot claim reliability"
    assert any(f"trials >= {MIN_TRIALS_FOR_PASS_POW_K}" in r for r in result.reasons)
    assert (result.min_pass_pow_k, result.actual_pass_pow_k) == (0.5, 1.0)


def test_gate_min_pass_pow_k_passes_and_fails_on_the_measured_value() -> None:
    passing = evaluate_gate(_trials_report(trials=2, pass_pow_k=0.5), min_pass_pow_k=0.5)
    assert passing.passed is True and passing.reasons == []
    failing = evaluate_gate(_trials_report(trials=2, pass_pow_k=0.5), min_pass_pow_k=0.9)
    assert failing.passed is False
    assert any("pass_pow_k 0.500 < min_pass_pow_k 0.900" in r for r in failing.reasons)


def test_gate_min_pass_pow_k_fails_when_the_report_has_no_measurement() -> None:
    unavailable = evaluate_gate(_trials_report(trials=2, pass_pow_k=None), min_pass_pow_k=0.5)
    assert unavailable.passed is False
    assert any("pass_pow_k unavailable" in r for r in unavailable.reasons)
    non_finite = evaluate_gate(
        _trials_report(trials=2, pass_pow_k=float("nan")), min_pass_pow_k=0.5
    )
    assert non_finite.passed is False


def test_gate_min_pass_pow_k_is_ignored_when_unset_and_on_an_empty_dataset() -> None:
    assert evaluate_gate(_trials_report(trials=1, pass_pow_k=0.0)).passed is True
    empty = evaluate_gate(
        _trials_report(trials=1, pass_pow_k=0.0, dataset_size=0), min_pass_pow_k=0.5
    )
    assert empty.passed is True and any("empty dataset" in r for r in empty.reasons)


def test_merge_carries_the_reliability_fields_from_the_threshold_verdict() -> None:
    threshold = evaluate_gate(_trials_report(trials=2, pass_pow_k=0.5), min_pass_pow_k=0.9)
    other = evaluate_gate(_trials_report(trials=2, pass_pow_k=0.5))
    merged = merge_gate_results([threshold, other])
    assert merged is not None and merged.passed is False
    assert (merged.min_pass_pow_k, merged.actual_pass_pow_k) == (0.9, 0.5)
