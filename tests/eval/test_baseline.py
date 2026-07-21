"""Tests for baseline loading (round-trips a json_file-sink artifact)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mangomas.errors import ConfigError
from mangomas.eval import evaluate_gate, load_baseline
from mangomas.eval.runner import EvalReport, EvalRowResult
from mangomas.eval.sinks.json_file import JsonFileSink


def _report() -> EvalReport:
    return EvalReport(
        scorer="exact_match",
        agent_name="chat",
        dataset_size=2,
        passed=2,
        failed=0,
        errored=0,
        mean_score=1.0,
        duration_ms=5.0,
        rows=[
            EvalRowResult(
                row_id="r1", score=1.0, passed=True, duration_ms=1.0, prediction="a", expected="a"
            ),
            EvalRowResult(
                row_id="r2",
                score=1.0,
                passed=True,
                duration_ms=1.0,
                prediction="b",
                expected="b",
                metadata={"k": 1},
            ),
        ],
        target_name="chat",
    )


async def test_load_baseline_round_trips_ignoring_gate(tmp_path: Path) -> None:
    report = _report()
    path = tmp_path / "baseline.json"
    # Write via the json_file sink *with* a gate key to prove it is ignored.
    await JsonFileSink(path=str(path)).emit(
        report, gate_result=evaluate_gate(report, min_mean_score=0.5)
    )
    assert await load_baseline(path) == report


async def test_load_baseline_tolerates_missing_target_name(tmp_path: Path) -> None:
    data = {
        "scorer": "exact_match",
        "agent_name": "chat",
        "dataset_size": 0,
        "passed": 0,
        "failed": 0,
        "errored": 0,
        "mean_score": 0.0,
        "duration_ms": 0.0,
        "rows": [],
    }
    path = tmp_path / "old.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert (await load_baseline(path)).target_name == ""


async def test_load_baseline_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        await load_baseline(tmp_path / "nope.json")


async def test_load_baseline_malformed_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        await load_baseline(path)


async def test_load_baseline_invalid_structure_raises(tmp_path: Path) -> None:
    path = tmp_path / "incomplete.json"
    path.write_text(json.dumps({"scorer": "x"}), encoding="utf-8")
    with pytest.raises(ConfigError):
        await load_baseline(path)
