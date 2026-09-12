"""Tests for the shared report-payload serializer used by sinks and baselines."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from mangomas.config import DEFAULT_EVAL_SCORER, DEFAULT_EVAL_TARGET
from mangomas.eval._serialize import report_payload
from mangomas.eval.baseline import load_baseline
from mangomas.eval.gate import GateResult
from mangomas.eval.runner import EvalReport, EvalRowResult
from tests.constants import DEFAULT_AGENT_NAME, EVAL_COST_USD_METADATA_KEY, STUB_REPLY

_ROW_ID = "row-0"
_DURATION_MS = 1.0


def _report() -> EvalReport:
    return EvalReport(
        scorer=DEFAULT_EVAL_SCORER,
        agent_name=DEFAULT_AGENT_NAME,
        dataset_size=1,
        passed=1,
        failed=0,
        errored=0,
        mean_score=1.0,
        duration_ms=_DURATION_MS,
        rows=[
            EvalRowResult(
                row_id=_ROW_ID,
                score=1.0,
                passed=True,
                duration_ms=_DURATION_MS,
                prediction=STUB_REPLY,
                expected=STUB_REPLY,
            )
        ],
        target_name=DEFAULT_EVAL_TARGET,
    )


def test_payload_matches_asdict_without_gate() -> None:
    report = _report()
    payload = report_payload(report)
    assert payload == dataclasses.asdict(report)
    assert "gate" not in payload


def test_payload_attaches_gate_verdict() -> None:
    report = _report()
    gate = GateResult(passed=True, actual_mean_score=1.0, actual_pass_rate=1.0)
    payload = report_payload(report, gate_result=gate)
    assert payload["gate"] == dataclasses.asdict(gate)
    # The report portion is unchanged by attaching a gate verdict.
    without_gate = {k: v for k, v in payload.items() if k != "gate"}
    assert without_gate == dataclasses.asdict(report)


async def test_payload_round_trips_through_load_baseline(tmp_path: Path) -> None:
    """The sink payload and the baseline parser are two halves of one contract.

    ``load_baseline`` reads exactly what the ``json_file`` sink writes, so the
    serializer must survive the round trip — including the extra ``"gate"`` key,
    which the parser is expected to tolerate.
    """
    report = _report()
    gate = GateResult(passed=True, actual_mean_score=1.0, actual_pass_rate=1.0)
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps(report_payload(report, gate_result=gate), ensure_ascii=False),
        encoding="utf-8",
    )

    restored = await load_baseline(str(path))

    assert restored == report


async def test_payload_round_trips_mean_cost_usd(tmp_path: Path) -> None:
    report = EvalReport(
        scorer=DEFAULT_EVAL_SCORER,
        agent_name=DEFAULT_AGENT_NAME,
        dataset_size=1,
        passed=1,
        failed=0,
        errored=0,
        mean_score=1.0,
        duration_ms=_DURATION_MS,
        rows=[
            EvalRowResult(
                row_id=_ROW_ID,
                score=1.0,
                passed=True,
                duration_ms=_DURATION_MS,
                prediction=STUB_REPLY,
                expected=STUB_REPLY,
                metadata={EVAL_COST_USD_METADATA_KEY: 0.05},
            )
        ],
        target_name=DEFAULT_EVAL_TARGET,
        mean_cost_usd=0.05,
    )
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(report_payload(report), ensure_ascii=False), encoding="utf-8")
    restored = await load_baseline(str(path))
    assert restored == report
    assert restored.mean_cost_usd == 0.05
    assert restored.rows[0].metadata[EVAL_COST_USD_METADATA_KEY] == 0.05
