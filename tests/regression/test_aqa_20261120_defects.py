"""Regression + AQA guards for the 2026-11-20 origin-delta validation.

Branch ``sdlc/origin-delta-aqa-20261120``, cut from ``origin/dev@e84e464``
(PR #153 already merged ``feature/qa-vertex-location-global``). Soft-deploy
Vertex ``LOCATION=global`` guards live in ``test_aqa_20261120_soft_deploy.py``.

Every product defect ``D-n`` is entered in ``docs/testing/rca-20261120.md``
**before** any ``src/`` change; guards here are verified RED on the pre-fix
tree, then GREEN after the minimal fix. See the RCA for lane matrices.
"""

from __future__ import annotations

import importlib
import json
import logging
import math
import sqlite3
from fractions import Fraction
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from mangomas.config import EVAL_COST_USD_METADATA_KEY
from mangomas.core import AgentContext, Orchestrator
from mangomas.errors import LLMBadResponse, ToolExecutionError
from mangomas.eval import (
    EvalReport,
    EvalRowResult,
    EvalRunner,
    evaluate_gate,
    evaluate_regression_gate,
)
from mangomas.eval._serialize import report_payload
from mangomas.eval.baseline import ReportDiff
from mangomas.eval.gate import _score_reasons
from mangomas.eval.judge_calibration import JudgeStatus
from mangomas.eval.protocol import ScorerContext
from mangomas.eval.runner import _mean_cost_usd, _row_cost_usd, collapse_trials
from mangomas.eval.scorers.exact_match import ExactMatchScorer
from mangomas.eval.scorers.llm_judge import LLMJudgeScorer
from mangomas.eval.sinks.sqlite_results import SqliteResultsSink
from mangomas.harness.isolation.base import IsolationResult, OracleIsolationBackend
from mangomas.harness.repair_sandbox import run_oracle_and_promote, stage_candidate_file
from tests._script_loader import load_script_module
from tests.fakes import FakeLLM, FakeRepository

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RCA_PATH = _REPO_ROOT / "docs" / "testing" / "rca-20261120.md"


def _require_numpy() -> Any:
    """Import ``numpy`` or fail loudly inside ``RUN_NUMPY=1`` tests."""
    try:
        return importlib.import_module("numpy")
    except ModuleNotFoundError as exc:  # pragma: no cover - asserted via no-numpy lane
        pytest.fail(f"RUN_NUMPY=1 requires numpy to be installed: {exc}")
        raise


def test_aqa_20261120_rca_register_exists() -> None:
    """Origin-delta AQA requires an RCA register before production edits."""
    assert _RCA_PATH.is_file(), f"missing RCA register: {_RCA_PATH}"
    text = _RCA_PATH.read_text(encoding="utf-8")
    assert "feature/qa-vertex-location-global" in text
    assert "merged" in text.lower()
    assert "e84e464" in text


def test_aqa_20261120_soft_deploy_companion_exists() -> None:
    """Soft-deploy Vertex LOCATION=global stays in its dedicated companion module."""
    companion = Path(__file__).with_name("test_aqa_20261120_soft_deploy.py")
    assert companion.is_file(), f"missing soft-deploy companion: {companion}"


# ── D-2: Fail-open thresholds & NumPy scalars in eval.gate ───────────────────


def _gate_report(
    *,
    dataset_size: int = 2,
    passed: int = 2,
    mean_score: float = 1.0,
    mean_cost_usd: float | None = 0.01,
    trials: int = 2,
    pass_pow_k: float | None = 1.0,
) -> EvalReport:
    return EvalReport(
        scorer="exact_match",
        agent_name="chat",
        dataset_size=dataset_size,
        passed=passed,
        failed=dataset_size - passed,
        errored=0,
        mean_score=mean_score,
        duration_ms=1.0,
        rows=[],
        mean_cost_usd=mean_cost_usd,
        trials=trials,
        pass_pow_k=pass_pow_k,
    )


def _gate_diff(*, mean_score_delta: float = 0.0, pass_rate_delta: float = 0.0) -> ReportDiff:
    return ReportDiff(
        baseline_mean_score=1.0,
        current_mean_score=1.0 + mean_score_delta,
        mean_score_delta=mean_score_delta,
        baseline_pass_rate=1.0,
        current_pass_rate=1.0 + pass_rate_delta,
        pass_rate_delta=pass_rate_delta,
        passed_delta=0,
        failed_delta=0,
        errored_delta=0,
    )


def test_d2_eval_gate_fails_closed_on_non_finite_and_bool_thresholds_and_accepts_numpy_scalars(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every threshold & metric in eval.gate rejects NaN/Inf/bool/out-of-range and accepts Reals."""
    report = _gate_report()
    bad_values: list[Any] = [float("nan"), float("inf"), float("-inf"), True, False, -0.1, 1.1]

    with caplog.at_level(logging.WARNING, logger="mangomas.eval.gate"):
        for bad in bad_values:
            r_mean = evaluate_gate(report, min_mean_score=bad)
            assert r_mean.passed is False, f"min_mean_score={bad!r} failed open"
            r_rate = evaluate_gate(report, min_pass_rate=bad)
            assert r_rate.passed is False, f"min_pass_rate={bad!r} failed open"
            r_pow = evaluate_gate(report, min_pass_pow_k=bad)
            assert r_pow.passed is False, f"min_pass_pow_k={bad!r} failed open"
            r_reg_mean = evaluate_regression_gate(_gate_diff(), max_mean_score_drop=bad)
            assert r_reg_mean.passed is False, f"max_mean_score_drop={bad!r} failed open"
            r_reg_rate = evaluate_regression_gate(_gate_diff(), max_pass_rate_drop=bad)
            assert r_reg_rate.passed is False, f"max_pass_rate_drop={bad!r} failed open"

        for bad_cost in (float("nan"), float("inf"), float("-inf"), True, False, -0.01):
            r_cost = evaluate_gate(report, max_mean_cost_usd=bad_cost)
            assert r_cost.passed is False, f"max_mean_cost_usd={bad_cost!r} failed open"

    assert any(
        getattr(rec, "event", None) == "eval_gate_invalid_threshold" for rec in caplog.records
    )

    non_finite_rate_reasons = _score_reasons(
        report,
        float("nan"),
        min_mean_score=None,
        min_pass_rate=0.5,
        min_pass_pow_k=None,
    )
    assert non_finite_rate_reasons == ["pass_rate is non-finite"]

    nan_rate_drop = evaluate_regression_gate(
        _gate_diff(pass_rate_delta=float("nan")),
        max_pass_rate_drop=0.1,
    )
    assert nan_rate_drop.passed is False
    assert nan_rate_drop.reasons == ["pass_rate drop is not a finite number"]
    assert (
        evaluate_regression_gate(
            _gate_diff(pass_rate_delta=0.0),
            max_pass_rate_drop=0.1,
        ).passed
        is True
    )

    ok_result = evaluate_gate(
        report,
        min_mean_score=Fraction(4, 5),  # type: ignore[arg-type]
        min_pass_rate=Fraction(4, 5),  # type: ignore[arg-type]
        min_pass_pow_k=Fraction(4, 5),  # type: ignore[arg-type]
        max_mean_cost_usd=Fraction(1, 20),  # type: ignore[arg-type]
    )
    assert ok_result.passed is True


def test_d2_uncalibrated_judge_and_empty_dataset_still_reject_invalid_thresholds() -> None:
    """Copilot #1: uncalibrated judge demotion and empty datasets never waive invalid limits."""
    uncalibrated = JudgeStatus(calibrated=False, reason="no calibration record", judge_model="m")
    report = EvalReport(
        scorer="llm_judge",
        agent_name="chat",
        dataset_size=2,
        passed=1,
        failed=1,
        errored=0,
        mean_score=0.4,
        duration_ms=1.0,
        rows=[],
        judge=uncalibrated,
    )
    for bad in (float("nan"), True, -0.1, 1.5):
        res_score = evaluate_gate(report, min_mean_score=bad)
        assert res_score.passed is False and res_score.judge_excluded is True
        res_rate = evaluate_gate(report, min_pass_rate=bad)
        assert res_rate.passed is False and res_rate.judge_excluded is True
        res_pow = evaluate_gate(report, min_pass_pow_k=bad)
        assert res_pow.passed is False and res_pow.judge_excluded is True
        res_reg = evaluate_regression_gate(
            _gate_diff(), max_mean_score_drop=bad, judge=uncalibrated
        )
        assert res_reg.passed is False and res_reg.judge_excluded is True

    empty = _gate_report(dataset_size=0, passed=0, mean_score=0.0)
    assert evaluate_gate(empty, min_mean_score=float("nan")).passed is False
    assert evaluate_gate(empty, max_mean_cost_usd=-1.0).passed is False


def test_d2_eval_gate_preserves_exact_real_operands_in_comparisons() -> None:
    """Copilot #2: sub-ULP ``Fraction`` differences are never lost to ``float()`` rounding."""
    tiny_over_half = Fraction(1, 2) + Fraction(1, 2**55)
    assert float(tiny_over_half) == 0.5
    half_report = _gate_report(dataset_size=2, passed=1, mean_score=0.5, trials=2, pass_pow_k=0.5)

    res_mean = evaluate_gate(half_report, min_mean_score=tiny_over_half)  # type: ignore[arg-type]
    assert res_mean.passed is False and any("mean_score" in r for r in res_mean.reasons)

    res_rate = evaluate_gate(half_report, min_pass_rate=tiny_over_half)  # type: ignore[arg-type]
    assert res_rate.passed is False and any("pass_rate" in r for r in res_rate.reasons)

    res_pow = evaluate_gate(half_report, min_pass_pow_k=tiny_over_half)  # type: ignore[arg-type]
    assert res_pow.passed is False and any("pass_pow_k" in r for r in res_pow.reasons)

    tiny_over_quarter = Fraction(1, 4) + Fraction(1, 2**55)
    cost_report = _gate_report(mean_cost_usd=tiny_over_quarter)  # type: ignore[arg-type]
    res_cost = evaluate_gate(cost_report, max_mean_cost_usd=0.25)
    assert res_cost.passed is False and any("mean_cost_usd" in r for r in res_cost.reasons)

    res_reg = evaluate_regression_gate(
        _gate_diff(mean_score_delta=-tiny_over_half),  # type: ignore[arg-type]
        max_mean_score_drop=0.5,
    )
    assert res_reg.passed is False and any("mean_score dropped" in r for r in res_reg.reasons)


def test_d2_regression_gate_validates_deltas_before_negation() -> None:
    """Copilot #3: boolean deltas fail closed instead of becoming ``-1`` / ``0`` after negation."""
    for bad_bool in (True, False):
        mean_res = evaluate_regression_gate(
            _gate_diff(mean_score_delta=bad_bool),
            max_mean_score_drop=0.1,
        )
        assert mean_res.passed is False
        assert mean_res.reasons == ["mean_score drop is not a finite number"]

        rate_res = evaluate_regression_gate(
            _gate_diff(pass_rate_delta=bad_bool),
            max_pass_rate_drop=0.1,
        )
        assert rate_res.passed is False
        assert rate_res.reasons == ["pass_rate drop is not a finite number"]


# ── D-3: NumPy scalars & bool counts in eval.runner & eval._serialize ────────


def test_d3_eval_runner_and_serialize_handle_numpy_scalars_and_reject_bool_counts() -> None:
    """Runner/serializer handle exact Reals/mappings cleanly and reject boolean counts."""
    orch = Orchestrator(AgentContext(llm=FakeLLM(), repo=FakeRepository()))

    for bad_bool in (True, False):
        with pytest.raises(ValueError, match="parallelism must be >= 1"):
            EvalRunner(orch, ExactMatchScorer(), parallelism=bad_bool)
        with pytest.raises(ValueError, match="trials must be >= 1"):
            EvalRunner(orch, ExactMatchScorer(), trials=bad_bool)

    def _cost_row(val: Any) -> EvalRowResult:
        return EvalRowResult(
            row_id="r1",
            score=1.0,
            passed=True,
            duration_ms=1.0,
            prediction="ok",
            expected="ok",
            metadata={EVAL_COST_USD_METADATA_KEY: val},
        )

    assert _row_cost_usd(_cost_row(True)) is None
    assert _row_cost_usd(_cost_row(Fraction(1, 4))) == pytest.approx(0.25)

    meta_dict: dict[str, Any] = {
        "ratio": Fraction(1, 2),
        "readonly": MappingProxyType({"k": Fraction(3, 2)}),
        "nan_val": float("nan"),
        "inf_val": float("inf"),
    }
    row = EvalRowResult(
        row_id="r-ser",
        score=1.0,
        passed=True,
        duration_ms=1.0,
        prediction="ok",
        expected="ok",
        metadata=meta_dict,
    )
    report = EvalReport(
        scorer="exact_match",
        agent_name="chat",
        dataset_size=1,
        passed=1,
        failed=0,
        errored=0,
        mean_score=1.0,
        duration_ms=1.0,
        rows=[row],
    )
    payload = report_payload(report)
    dumped = json.dumps(payload, allow_nan=False)
    decoded = json.loads(dumped)
    row_meta = decoded["rows"][0]["metadata"]
    assert row_meta["readonly"] == {"k": 1.5}
    assert row_meta["nan_val"] is None
    assert row_meta["inf_val"] is None


def test_d3_runner_propagates_overflowing_reported_costs_to_cost_gate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Copilot #4: overflowing reported costs fail the cost gate instead of skipping as None."""

    def _cost_row(row_id: str, val: Any) -> EvalRowResult:
        return EvalRowResult(
            row_id=row_id,
            score=1.0,
            passed=True,
            duration_ms=1.0,
            prediction="ok",
            expected="ok",
            metadata={EVAL_COST_USD_METADATA_KEY: val},
        )

    with caplog.at_level(logging.WARNING, logger="mangomas.eval.runner"):
        overflow_mean = _mean_cost_usd([_cost_row("r1", 10**400)])
        assert overflow_mean is not None and math.isinf(overflow_mean)

        mixed_mean = _mean_cost_usd([_cost_row("r1", 1.0), _cost_row("r2", 10**400)])
        assert mixed_mean is not None and math.isinf(mixed_mean)

        opposite_mean = _mean_cost_usd([_cost_row("r1", 10**400), _cost_row("r2", -(10**400))])
        assert opposite_mean is not None and math.isnan(opposite_mean)

        sum_overflow_mean = _mean_cost_usd([_cost_row("r1", 1e308), _cost_row("r2", 1e308)])
        assert sum_overflow_mean is not None and math.isinf(sum_overflow_mean)

        collapsed = collapse_trials([_cost_row("r1", 0.1), _cost_row("r1", 10**400)])
        collapsed_mean = _mean_cost_usd([collapsed])
        assert collapsed_mean is not None and math.isinf(collapsed_mean)

    assert any(getattr(rec, "event", None) == "eval_row_cost_overflow" for rec in caplog.records)

    gate_res = evaluate_gate(
        _gate_report(dataset_size=1, passed=1, mean_cost_usd=overflow_mean),
        max_mean_cost_usd=0.05,
    )
    assert gate_res.passed is False
    assert "mean_cost_usd is not a finite number" in gate_res.reasons


class _StubIsolation(OracleIsolationBackend):
    name = "stub"

    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def is_available(self) -> bool:
        return True

    def run(
        self,
        argv: Any,
        *,
        cwd: Path,
        env: Any,
        timeout_seconds: float,
    ) -> IsolationResult:
        del argv, cwd, env
        self.timeouts.append(timeout_seconds)
        return IsolationResult(returncode=0, stdout="", stderr="", backend=self.name)


@pytest.mark.numpy
async def test_aqa_20261120_real_numpy_regressions(tmp_path: Path) -> None:
    """Guaranteed CI/local regression coverage with real NumPy installed (``RUN_NUMPY=1``)."""
    np = _require_numpy()
    report = _gate_report()
    for bad in (np.float32("nan"), np.float64("inf"), np.bool_(True)):
        assert evaluate_gate(report, min_mean_score=bad).passed is False
        assert evaluate_gate(report, max_mean_cost_usd=bad).passed is False
        assert evaluate_regression_gate(_gate_diff(), max_mean_score_drop=bad).passed is False
    assert (
        evaluate_regression_gate(
            _gate_diff(mean_score_delta=np.bool_(True)),
            max_mean_score_drop=0.1,
        ).passed
        is False
    )
    assert (
        evaluate_gate(
            report,
            min_mean_score=np.float32(0.8),
            min_pass_rate=np.float32(0.8),
            min_pass_pow_k=np.float32(0.8),
            max_mean_cost_usd=np.float64(0.05),
        ).passed
        is True
    )

    orch = Orchestrator(AgentContext(llm=FakeLLM(), repo=FakeRepository()))
    with pytest.raises(ValueError, match="parallelism must be >= 1"):
        EvalRunner(orch, ExactMatchScorer(), parallelism=np.bool_(True))

    row = EvalRowResult(
        row_id="r-np",
        score=np.float32(0.75),
        passed=np.bool_(True),
        duration_ms=np.float64(2.5),
        prediction="ok",
        expected="ok",
        metadata={
            EVAL_COST_USD_METADATA_KEY: np.float32(0.25),
            "np_nan": np.float32("nan"),
            "np_inf": np.float64("inf"),
            "np_i64": np.int64(7),
            "np_bool": np.bool_(True),
        },
    )
    assert _row_cost_usd(row) == pytest.approx(0.25)
    assert _row_cost_usd(
        EvalRowResult(
            row_id="r-int",
            score=1.0,
            passed=True,
            duration_ms=1.0,
            prediction="ok",
            expected="ok",
            metadata={EVAL_COST_USD_METADATA_KEY: np.int64(2)},
        )
    ) == pytest.approx(2.0)
    assert (
        _row_cost_usd(
            EvalRowResult(
                row_id="r-bad-bool",
                score=1.0,
                passed=True,
                duration_ms=1.0,
                prediction="ok",
                expected="ok",
                metadata={EVAL_COST_USD_METADATA_KEY: np.bool_(True)},
            )
        )
        is None
    )
    inf_cost = _row_cost_usd(
        EvalRowResult(
            row_id="r-inf",
            score=1.0,
            passed=True,
            duration_ms=1.0,
            prediction="ok",
            expected="ok",
            metadata={EVAL_COST_USD_METADATA_KEY: np.float64("inf")},
        )
    )
    assert inf_cost is not None and math.isinf(inf_cost)

    np_report = EvalReport(
        scorer="exact_match",
        agent_name="chat",
        dataset_size=1,
        passed=1,
        failed=0,
        errored=0,
        mean_score=np.float32(0.75),
        duration_ms=np.float64(2.5),
        mean_cost_usd=np.float32(0.25),
        pass_pow_k=np.float64(1.0),
        rows=[row],
    )
    decoded = json.loads(json.dumps(report_payload(np_report), allow_nan=False))
    row_meta = decoded["rows"][0]["metadata"]
    assert row_meta["np_nan"] is None and row_meta["np_inf"] is None
    assert row_meta["np_i64"] == 7 and row_meta["np_bool"] is True

    np_gate = evaluate_gate(
        np_report,
        min_mean_score=np.float32(0.75),
        max_mean_cost_usd=np.float32(0.25),
    )
    db = tmp_path / "np-eval.db"
    await SqliteResultsSink(db_path=str(db)).emit(np_report, gate_result=np_gate)
    with sqlite3.connect(db) as conn:
        rep_vals = conn.execute(
            "SELECT mean_score, duration_ms, mean_cost_usd, gate_json FROM eval_reports"
        ).fetchone()
        row_vals = conn.execute(
            "SELECT score, passed, duration_ms, metadata_json FROM eval_rows"
        ).fetchone()
    # NumPy scalars must bind as native SQLite float/int, never raw bytes BLOBs.
    assert type(rep_vals[0]) is float and rep_vals[:3] == (0.75, 2.5, 0.25)
    assert json.loads(rep_vals[3])["passed"] is True
    assert type(row_vals[0]) is float and type(row_vals[1]) is int
    assert json.loads(row_vals[3])["np_i64"] == 7

    staging = tmp_path / "staging"
    repo = tmp_path / "repo"
    staging.mkdir()
    repo.mkdir()
    rel = "src/mangomas/agents/chat.py"
    stage_candidate_file(staging, rel, "VALUE = 1\n")
    for bad_to in (np.bool_(True), np.float32("nan")):
        with pytest.raises(ToolExecutionError, match="positive and finite"):
            run_oracle_and_promote(
                staging_root=staging,
                repo_root=repo,
                relative_path=rel,
                allowed_paths={rel},
                oracle_argv=["python", "-c", "pass"],
                timeout_seconds=bad_to,
            )
    stub = _StubIsolation()
    outcome = run_oracle_and_promote(
        staging_root=staging,
        repo_root=repo,
        relative_path=rel,
        allowed_paths={rel},
        oracle_argv=["python", "-c", "pass"],
        timeout_seconds=np.float32(5.0),
        isolation=stub,
    )
    assert outcome.promoted is True
    assert stub.timeouts == [5.0] and type(stub.timeouts[0]) is float


# ── D-4: Boolean score coercion & OverflowError in llm_judge ─────────────────


@pytest.mark.parametrize(
    "raw_reply",
    [
        '{"score": true, "rationale": "bool true"}',
        '{"score": false, "rationale": "bool false"}',
        '{"score": ' + ("9" * 400) + ', "rationale": "overflow int"}',
    ],
)
async def test_d4_llm_judge_rejects_boolean_and_overflow_scores(
    raw_reply: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Boolean or int-overflow judge scores must raise LLMBadResponse, never coerce/crash."""
    scorer = LLMJudgeScorer(threshold=0.7)
    with (
        caplog.at_level(logging.DEBUG, logger="mangomas.eval.scorers.llm_judge"),
        pytest.raises(LLMBadResponse, match="not numeric"),
    ):
        await scorer.score(
            "prediction",
            "expected",
            context=ScorerContext(llm=FakeLLM(reply=raw_reply)),
        )
    assert any(getattr(rec, "event", None) == "eval_llm_judge_bad_score" for rec in caplog.records)


# ── D-5: Boolean timeout_seconds & Real scalars in repair_sandbox ──────────────


def test_d5_repair_sandbox_rejects_bool_timeout_and_accepts_numpy_real(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``run_oracle_and_promote`` rejects ``bool`` timeout_seconds and normalizes finite Reals."""
    staging = tmp_path / "staging"
    repo = tmp_path / "repo"
    staging.mkdir()
    repo.mkdir()
    rel = "src/mangomas/agents/chat.py"
    stage_candidate_file(staging, rel, "VALUE = 1\n")

    bad_timeouts: list[Any] = [True, False, float("nan"), float("inf")]

    with caplog.at_level(logging.WARNING, logger="mangomas.harness.repair_sandbox"):
        for bad in bad_timeouts:
            with pytest.raises(ToolExecutionError, match="positive and finite"):
                run_oracle_and_promote(
                    staging_root=staging,
                    repo_root=repo,
                    relative_path=rel,
                    allowed_paths={rel},
                    oracle_argv=["python", "-c", "pass"],
                    timeout_seconds=bad,
                )
    assert any(
        getattr(rec, "event", None) == "repair_sandbox_invalid_timeout" for rec in caplog.records
    )

    stub = _StubIsolation()
    outcome = run_oracle_and_promote(
        staging_root=staging,
        repo_root=repo,
        relative_path=rel,
        allowed_paths={rel},
        oracle_argv=["python", "-c", "pass"],
        timeout_seconds=Fraction(5, 1),  # type: ignore[arg-type]
        isolation=stub,
    )
    assert outcome.promoted is True
    assert stub.timeouts == [5.0]
    assert type(stub.timeouts[0]) is float


# ── D-6: Boolean schema_version in deploy_environment ────────────────────────


def test_d6_deploy_registry_rejects_boolean_schema_version() -> None:
    """``parse_registry`` must reject ``schema_version: true`` (Python ``True == 1``)."""
    deploy_env = load_script_module("deploy_environment.py")
    doc = {
        "schema_version": True,
        "defaults": {
            "region": "us-central1",
            "repository": "mangomas",
            "image": "mangomas-api",
            "base_manifest": "deploy/service.yaml",
        },
        "environments": {
            "dev": {
                "service": "mangomas-dev",
                "trigger": {"branch": "dev"},
            }
        },
    }
    with pytest.raises(deploy_env.DeployConfigError, match="schema_version"):
        deploy_env.parse_registry(doc)
