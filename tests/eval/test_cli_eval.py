"""Tests for the ``mangomas eval`` CLI subcommand."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

# Importing the CLI module also wires the scorer registry.
import mangomas.cli.main as cli_main
from mangomas.cli.main import app
from mangomas.config import get_settings
from mangomas.core import Orchestrator
from mangomas.eval import Sink
from mangomas.eval.runner import EvalReport
from tests.constants import EVAL_GATE_EXIT_CODE, EVAL_THRESHOLD_LENIENT, EVAL_THRESHOLD_STRICT
from tests.fakes import FakeSink


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    """Each test sees a freshly-loaded ``Settings`` so env overrides apply."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _stub_cli_orchestrator(
    eval_orchestrator: Orchestrator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replace ``_build()`` with a fake orchestrator so the CLI test never
    tries to reach the configured LLM provider (LM Studio is not running in
    the unit-test environment)."""
    monkeypatch.setattr(cli_main, "_build", lambda: eval_orchestrator)


def test_eval_cli_runs_against_fixture(fixtures_dir: Path) -> None:
    """End-to-end CLI invocation: --dataset → orchestrator → scorer → stdout."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "all_pass.jsonl"),
            "--scorer",
            "exact_match",
            "--agent",
            "chat",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "scorer=exact_match" in result.stdout
    assert "passed=2" in result.stdout


def test_eval_cli_target_echo(fixtures_dir: Path) -> None:
    """--target echo dispatches the deterministic baseline (no agent needed)."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "all_pass.jsonl"),
            "--scorer",
            "exact_match",
            "--target",
            "echo",
        ],
    )
    assert result.exit_code == 0, result.stdout
    # echo returns the user message ("say stub"/"again"), which never matches
    # the expected "stub-reply", so the baseline fails every row.
    assert "agent=echo" in result.stdout
    assert "passed=0" in result.stdout


def test_eval_cli_target_agent_with_agent_flag(fixtures_dir: Path) -> None:
    """--target agent --agent chat preserves the legacy single-agent behaviour."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "all_pass.jsonl"),
            "--scorer",
            "exact_match",
            "--target",
            "agent",
            "--agent",
            "chat",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "agent=chat" in result.stdout
    assert "passed=2" in result.stdout


def test_eval_cli_unknown_target_exits_2(fixtures_dir: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "all_pass.jsonl"),
            "--scorer",
            "exact_match",
            "--target",
            "definitely-not-a-target",
        ],
    )
    assert result.exit_code == 2
    assert "definitely-not-a-target" in result.stdout + result.stderr


def test_eval_cli_missing_dataset_exits_2() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["eval"])
    assert result.exit_code == 2
    assert "No dataset path provided" in result.stdout + result.stderr


def test_eval_cli_inline_dataset_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """--dataset-source inline reads rows from MANGOMAS_EVAL__DATASET_SOURCE_OPTIONS."""
    rows = [{"id": "i1", "messages": [{"role": "user", "content": "x"}], "expected": "stub-reply"}]
    monkeypatch.setenv(
        "MANGOMAS_EVAL__DATASET_SOURCE_OPTIONS",
        json.dumps({"inline": {"rows": rows}}),
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["eval", "--dataset-source", "inline", "--scorer", "exact_match"],
    )
    assert result.exit_code == 0, result.stdout
    assert "size=1" in result.stdout
    assert "passed=1" in result.stdout


def test_eval_cli_unknown_dataset_source_exits_2(fixtures_dir: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "all_pass.jsonl"),
            "--scorer",
            "exact_match",
            "--dataset-source",
            "definitely-not-a-source",
        ],
    )
    assert result.exit_code == 2
    assert "definitely-not-a-source" in result.stdout + result.stderr


def test_eval_cli_unknown_scorer_exits_2(fixtures_dir: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "all_pass.jsonl"),
            "--scorer",
            "definitely-not-a-scorer",
        ],
    )
    assert result.exit_code == 2
    assert "definitely-not-a-scorer" in result.stdout + result.stderr


def test_eval_cli_writes_json_report(fixtures_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "report.json"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "mixed.jsonl"),
            "--scorer",
            "exact_match",
            "--output-json",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["scorer"] == "exact_match"
    assert payload["dataset_size"] == 3
    assert isinstance(payload["rows"], list)


def test_eval_cli_propagates_dataset_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json\n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["eval", "--dataset", str(bad), "--scorer", "exact_match"],
    )
    assert result.exit_code == 1
    assert "Eval run failed" in result.stdout + result.stderr


# ── Gating ────────────────────────────────────────────────────────────────────


def test_eval_cli_gate_fail_exits_3(fixtures_dir: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "mixed.jsonl"),
            "--scorer",
            "exact_match",
            "--min-mean-score",
            str(EVAL_THRESHOLD_STRICT),
        ],
    )
    assert result.exit_code == EVAL_GATE_EXIT_CODE, result.stdout
    assert "gate=FAIL" in result.stdout


def test_eval_cli_gate_pass_exits_0(fixtures_dir: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "all_pass.jsonl"),
            "--scorer",
            "exact_match",
            "--min-mean-score",
            str(EVAL_THRESHOLD_LENIENT),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "gate=PASS" in result.stdout


def test_eval_cli_min_pass_rate_fail_exits_3(fixtures_dir: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "mixed.jsonl"),
            "--scorer",
            "exact_match",
            "--min-pass-rate",
            "0.9",
        ],
    )
    assert result.exit_code == EVAL_GATE_EXIT_CODE


def test_eval_cli_gate_failure_still_writes_artifacts(fixtures_dir: Path, tmp_path: Path) -> None:
    """A failing gate must not prevent sink output (exit 3 comes after emit)."""
    out = tmp_path / "report.json"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "mixed.jsonl"),
            "--scorer",
            "exact_match",
            "--min-mean-score",
            str(EVAL_THRESHOLD_STRICT),
            "--output-json",
            str(out),
        ],
    )
    assert result.exit_code == EVAL_GATE_EXIT_CODE
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["gate"]["passed"] is False


def test_eval_cli_no_gate_overrides_configured_threshold(
    fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--no-gate` must disable gating even when a threshold is set via env."""
    monkeypatch.setenv("MANGOMAS_EVAL__MIN_MEAN_SCORE", "0.99")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "mixed.jsonl"),
            "--scorer",
            "exact_match",
            "--no-gate",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "gate=" not in result.stdout


def test_eval_cli_invalid_threshold_exits_2(fixtures_dir: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "all_pass.jsonl"),
            "--scorer",
            "exact_match",
            "--min-mean-score",
            "1.5",
        ],
    )
    assert result.exit_code == 2
    assert "must be in [0.0, 1.0]" in (result.stdout + result.stderr)


# ── Sinks ─────────────────────────────────────────────────────────────────────


def test_eval_cli_unknown_sink_exits_2(fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_EVAL__SINKS", '["console", "does-not-exist"]')
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["eval", "--dataset", str(fixtures_dir / "all_pass.jsonl"), "--scorer", "exact_match"],
    )
    assert result.exit_code == 2
    assert "configuration error" in (result.stdout + result.stderr).lower()


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
        rows=[],
    )


def test_emit_sinks_isolates_failures() -> None:
    good_a = FakeSink(name="a")
    boom = FakeSink(name="boom", raise_on_emit=RuntimeError("sink down"))
    good_b = FakeSink(name="b")
    sinks: list[Sink] = [good_a, boom, good_b]
    exc = asyncio.run(cli_main._emit_sinks(sinks, _report(), None))
    assert isinstance(exc, RuntimeError)
    # Both healthy sinks still emitted despite the failure in between.
    assert len(good_a.emitted) == 1
    assert len(good_b.emitted) == 1


def test_emit_sinks_returns_none_when_all_succeed() -> None:
    sinks: list[Sink] = [FakeSink(name="a"), FakeSink(name="b")]
    exc = asyncio.run(cli_main._emit_sinks(sinks, _report(), None))
    assert exc is None
