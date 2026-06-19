"""Tests for diff_reports — explicit cases + Hypothesis invariants."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mangomas.eval import diff_reports
from mangomas.eval.runner import EvalReport, EvalRowResult


def _row(row_id: str, *, passed: bool, error: str | None = None) -> EvalRowResult:
    return EvalRowResult(
        row_id=row_id,
        score=1.0 if passed else 0.0,
        passed=passed,
        duration_ms=1.0,
        prediction="p",
        expected="e",
        error=error,
    )


def _report(
    rows: list[EvalRowResult], *, mean_score: float, passed: int, failed: int = 0, errored: int = 0
) -> EvalReport:
    return EvalReport(
        scorer="s",
        agent_name="a",
        dataset_size=len(rows),
        passed=passed,
        failed=failed,
        errored=errored,
        mean_score=mean_score,
        duration_ms=1.0,
        rows=rows,
        target_name="a",
    )


def test_diff_detects_regression_new_and_dropped() -> None:
    baseline = _report([_row("r1", passed=True), _row("r2", passed=True)], mean_score=1.0, passed=2)
    current = _report(
        [_row("r1", passed=True), _row("r2", passed=False), _row("r3", passed=True)],
        mean_score=0.667,
        passed=2,
        failed=1,
    )
    diff = diff_reports(baseline, current)
    assert diff.regressed_rows == ["r2"]
    assert diff.new_rows == ["r3"]
    assert diff.dropped_rows == []
    assert diff.mean_score_delta == pytest.approx(0.667 - 1.0)


def test_diff_dropped_rows() -> None:
    baseline = _report([_row("r1", passed=True), _row("r2", passed=True)], mean_score=1.0, passed=2)
    current = _report([_row("r1", passed=True)], mean_score=1.0, passed=1)
    diff = diff_reports(baseline, current)
    assert diff.dropped_rows == ["r2"]
    assert diff.regressed_rows == []


def test_diff_errored_row_counts_as_regression() -> None:
    baseline = _report([_row("r1", passed=True)], mean_score=1.0, passed=1)
    current = _report([_row("r1", passed=False, error="boom")], mean_score=0.0, passed=0, errored=1)
    assert diff_reports(baseline, current).regressed_rows == ["r1"]


@st.composite
def _reports(draw: st.DrawFn) -> EvalReport:
    ids = draw(st.lists(st.sampled_from([f"r{i}" for i in range(6)]), unique=True, max_size=6))
    rows = [_row(rid, passed=draw(st.booleans())) for rid in ids]
    passed = sum(1 for r in rows if r.passed)
    mean = (sum(r.score for r in rows) / len(rows)) if rows else 0.0
    return _report(rows, mean_score=mean, passed=passed, failed=len(rows) - passed)


@given(baseline=_reports(), current=_reports())
def test_diff_invariants(baseline: EvalReport, current: EvalReport) -> None:
    diff = diff_reports(baseline, current)
    assert diff.mean_score_delta == pytest.approx(current.mean_score - baseline.mean_score)
    base_ids = {r.row_id for r in baseline.rows}
    cur_ids = {r.row_id for r in current.rows}
    assert set(diff.regressed_rows) <= (base_ids & cur_ids)
    assert set(diff.new_rows) == (cur_ids - base_ids)
    assert set(diff.dropped_rows) == (base_ids - cur_ids)
