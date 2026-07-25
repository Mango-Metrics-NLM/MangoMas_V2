"""Tests for the shared report-payload serializer used by sinks and baselines."""

from __future__ import annotations

import dataclasses

from mangomas.eval._serialize import report_payload
from mangomas.eval.gate import GateResult
from mangomas.eval.runner import EvalReport, EvalRowResult


def _report() -> EvalReport:
    return EvalReport(
        scorer="exact_match",
        agent_name="chat",
        dataset_size=1,
        passed=1,
        failed=0,
        errored=0,
        mean_score=1.0,
        duration_ms=1.0,
        rows=[
            EvalRowResult(
                row_id="row-0",
                score=1.0,
                passed=True,
                duration_ms=1.0,
                prediction="p",
                expected="p",
            )
        ],
        target_name="agent",
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
