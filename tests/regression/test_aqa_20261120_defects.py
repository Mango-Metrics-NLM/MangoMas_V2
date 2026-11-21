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
from mangomas.eval.protocol import ScorerContext
from mangomas.eval.runner import _row_cost_usd
from mangomas.eval.scorers.exact_match import ExactMatchScorer
from mangomas.eval.scorers.llm_judge import LLMJudgeScorer
from mangomas.harness.isolation.base import IsolationResult, OracleIsolationBackend
from mangomas.harness.repair_sandbox import run_oracle_and_promote, stage_candidate_file
from tests._script_loader import load_script_module
from tests.fakes import FakeLLM, FakeRepository

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RCA_PATH = _REPO_ROOT / "docs" / "testing" / "rca-20261120.md"


def _numpy_or_none() -> Any:
    try:
        return importlib.import_module("numpy")
    except ModuleNotFoundError:
        return None


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
    """Every threshold & metric in eval.gate rejects NaN/Inf/bool and accepts finite Reals."""
    report = _gate_report()
    bad_values: list[Any] = [float("nan"), float("inf"), float("-inf"), True, False]
    np_mod = _numpy_or_none()
    if np_mod is not None:
        bad_values.extend([np_mod.float32("nan"), np_mod.bool_(True)])

    with caplog.at_level(logging.WARNING, logger="mangomas.eval.gate"):
        for bad in bad_values:
            r_mean = evaluate_gate(report, min_mean_score=bad)
            assert r_mean.passed is False, f"min_mean_score={bad!r} failed open"
            r_rate = evaluate_gate(report, min_pass_rate=bad)
            assert r_rate.passed is False, f"min_pass_rate={bad!r} failed open"
            r_pow = evaluate_gate(report, min_pass_pow_k=bad)
            assert r_pow.passed is False, f"min_pass_pow_k={bad!r} failed open"
            r_cost = evaluate_gate(report, max_mean_cost_usd=bad)
            assert r_cost.passed is False, f"max_mean_cost_usd={bad!r} failed open"
            r_reg_mean = evaluate_regression_gate(_gate_diff(), max_mean_score_drop=bad)
            assert r_reg_mean.passed is False, f"max_mean_score_drop={bad!r} failed open"
            r_reg_rate = evaluate_regression_gate(_gate_diff(), max_pass_rate_drop=bad)
            assert r_reg_rate.passed is False, f"max_pass_rate_drop={bad!r} failed open"

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

    real_ok: Any = Fraction(4, 5)
    real_cost: Any = Fraction(1, 20)
    if np_mod is not None:
        real_ok = np_mod.float32(0.8)
        real_cost = np_mod.float64(0.05)

    ok_result = evaluate_gate(
        report,
        min_mean_score=real_ok,
        min_pass_rate=real_ok,
        min_pass_pow_k=real_ok,
        max_mean_cost_usd=real_cost,
    )
    assert ok_result.passed is True


# ── D-3: NumPy scalars & bool counts in eval.runner & eval._serialize ────────


def test_d3_eval_runner_and_serialize_handle_numpy_scalars_and_reject_bool_counts() -> None:
    """Runner/serializer handle NumPy scalars cleanly and reject boolean parallelism/trials."""
    np_mod = _numpy_or_none()
    orch = Orchestrator(AgentContext(llm=FakeLLM(), repo=FakeRepository()))

    for bad_bool in (True, False):
        with pytest.raises(ValueError, match="parallelism must be >= 1"):
            EvalRunner(orch, ExactMatchScorer(), parallelism=bad_bool)
        with pytest.raises(ValueError, match="trials must be >= 1"):
            EvalRunner(orch, ExactMatchScorer(), trials=bad_bool)

    if np_mod is not None:
        with pytest.raises(ValueError, match="parallelism must be >= 1"):
            EvalRunner(orch, ExactMatchScorer(), parallelism=np_mod.bool_(True))

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
    }
    if np_mod is not None:
        assert _row_cost_usd(_cost_row(np_mod.float32(0.25))) == pytest.approx(0.25)
        assert _row_cost_usd(_cost_row(np_mod.int64(2))) == pytest.approx(2.0)
        assert _row_cost_usd(_cost_row(np_mod.bool_(True))) is None
        assert _row_cost_usd(_cost_row(np_mod.float32("nan"))) is None
        meta_dict.update(
            {
                "np_f32": np_mod.float32(0.5),
                "np_nan": np_mod.float32("nan"),
                "np_inf": np_mod.float64("inf"),
                "np_i64": np_mod.int64(7),
                "np_bool": np_mod.bool_(True),
            }
        )

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
    if np_mod is not None:
        assert row_meta["np_nan"] is None
        assert row_meta["np_inf"] is None
        assert row_meta["np_i64"] == 7
        assert row_meta["np_bool"] is True


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


# ── D-5: Boolean timeout_seconds & NumPy Real in repair_sandbox ──────────────


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

    bad_timeouts: list[Any] = [True, False]
    np_mod = _numpy_or_none()
    if np_mod is not None:
        bad_timeouts.extend([np_mod.bool_(True), np_mod.float32("nan")])

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
    good_timeout: Any = np_mod.float32(5.0) if np_mod is not None else Fraction(5, 1)
    outcome = run_oracle_and_promote(
        staging_root=staging,
        repo_root=repo,
        relative_path=rel,
        allowed_paths={rel},
        oracle_argv=["python", "-c", "pass"],
        timeout_seconds=good_timeout,
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
