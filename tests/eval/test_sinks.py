"""Tests for built-in eval sinks (console + json_file) and the sink registry."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

# Importing this package registers the built-in sinks.
import mangomas.eval.sinks  # noqa: F401
from mangomas.errors import ConfigError
from mangomas.eval import GateResult, Sink, evaluate_gate, sink_registry
from mangomas.eval.runner import EvalReport, EvalRowResult
from mangomas.eval.sinks.console import ConsoleSink
from mangomas.eval.sinks.json_file import JsonFileSink
from tests.constants import EVAL_SINK_CONSOLE, EVAL_SINK_JSON_FILE


def _report() -> EvalReport:
    rows = [
        EvalRowResult(
            row_id="row-0",
            score=1.0,
            passed=True,
            duration_ms=1.0,
            prediction="p",
            expected="p",
        ),
        EvalRowResult(
            row_id="row-1",
            score=0.0,
            passed=False,
            duration_ms=1.0,
            prediction="x",
            expected="y",
        ),
    ]
    return EvalReport(
        scorer="exact_match",
        agent_name="chat",
        dataset_size=2,
        passed=1,
        failed=1,
        errored=0,
        mean_score=0.5,
        duration_ms=2.0,
        rows=rows,
    )


async def test_console_sink_writes_summary_and_rows() -> None:
    lines: list[str] = []
    sink = ConsoleSink(write=lines.append)
    await sink.emit(_report())
    assert any("scorer=exact_match" in line and "passed=1" in line for line in lines)
    assert any("[PASS] row-0" in line for line in lines)
    assert any("[FAIL] row-1" in line for line in lines)


async def test_console_sink_renders_gate_line() -> None:
    lines: list[str] = []
    sink = ConsoleSink(write=lines.append)
    gate = evaluate_gate(_report(), min_mean_score=0.9)
    await sink.emit(_report(), gate_result=gate)
    assert any(line.startswith("gate=FAIL") and "mean_score" in line for line in lines)


async def test_json_file_sink_round_trips(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "report.json"
    sink = JsonFileSink(path=str(out))
    gate = evaluate_gate(_report(), min_mean_score=0.1)
    await sink.emit(_report(), gate_result=gate)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["scorer"] == "exact_match"
    assert payload["dataset_size"] == 2
    assert isinstance(payload["rows"], list)
    assert payload["gate"]["passed"] is True


async def test_json_file_sink_omits_gate_when_absent(tmp_path: Path) -> None:
    out = tmp_path / "report.json"
    await JsonFileSink(path=str(out)).emit(_report())
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert "gate" not in payload


def test_json_file_factory_requires_path() -> None:
    factory = sink_registry.get(EVAL_SINK_JSON_FILE)
    with pytest.raises(ConfigError):
        factory({})


def test_builtin_sinks_registered() -> None:
    assert EVAL_SINK_CONSOLE in sink_registry.available()
    assert EVAL_SINK_JSON_FILE in sink_registry.available()


def test_console_and_json_factories_build_sinks(tmp_path: Path) -> None:
    console = sink_registry.get(EVAL_SINK_CONSOLE)({})
    json_sink = sink_registry.get(EVAL_SINK_JSON_FILE)({"path": str(tmp_path / "r.json")})
    assert isinstance(console, Sink)
    assert isinstance(json_sink, Sink)


def test_gate_result_serialises_in_json_payload() -> None:
    # GateResult must be asdict-able for the json_file payload.
    gate = GateResult(passed=True, actual_mean_score=1.0, actual_pass_rate=1.0)
    assert dataclasses.asdict(gate)["passed"] is True
