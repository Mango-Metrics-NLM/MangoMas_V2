"""Tests for the eval quality gate (``mangomas.eval.gate``)."""

from __future__ import annotations

import logging

import pytest

from mangomas.eval import GateResult, evaluate_gate
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


def test_gate_emits_structured_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="mangomas.eval.gate"):
        evaluate_gate(_report(dataset_size=2, passed=2), min_mean_score=0.5)
    events = [rec for rec in caplog.records if getattr(rec, "event", None) == "eval_gate"]
    assert events, "expected an eval_gate log record"
