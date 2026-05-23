"""Tests for the ``mangomas eval`` CLI subcommand."""

from __future__ import annotations

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


def test_eval_cli_missing_dataset_exits_2() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["eval"])
    assert result.exit_code == 2
    assert "No dataset path provided" in result.stdout + result.stderr


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
