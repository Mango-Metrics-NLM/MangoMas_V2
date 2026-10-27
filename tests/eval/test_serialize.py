"""Shared report payload builder (``mangomas.eval._serialize``)."""

from __future__ import annotations

import json
import math

from mangomas.eval._serialize import report_payload
from mangomas.eval.runner import EvalReport, EvalRowResult


def _report_with(score: float) -> EvalReport:
    return EvalReport(
        scorer="plugin",
        agent_name="chat",
        dataset_size=1,
        passed=0,
        failed=1,
        errored=0,
        mean_score=score,
        duration_ms=1.0,
        rows=[
            EvalRowResult(
                row_id="r1",
                score=score,
                passed=False,
                duration_ms=1.0,
                prediction="p",
                expected="e",
                metadata={"nested": {"ratio": score, "count": 3}},
            )
        ],
        target_name="agent",
    )


def test_report_payload_replaces_non_finite_floats_with_null() -> None:
    """``json.dumps`` defaults to ``allow_nan=True`` and would otherwise write a
    non-RFC-8259 ``NaN`` token into the baseline artefact. Mutation: return the
    raw ``asdict`` -> ``json.dumps`` output contains ``NaN``."""
    payload = report_payload(_report_with(float("nan")))
    assert payload["mean_score"] is None
    assert payload["rows"][0]["score"] is None
    assert payload["rows"][0]["metadata"]["nested"]["ratio"] is None
    assert payload["rows"][0]["metadata"]["nested"]["count"] == 3
    assert "NaN" not in json.dumps(payload)


def test_report_payload_keeps_finite_values_untouched() -> None:
    payload = report_payload(_report_with(0.25))
    assert math.isclose(payload["mean_score"], 0.25)
    assert payload["rows"][0]["metadata"]["nested"]["count"] == 3
