"""Tests for the ``mangomas eval`` CLI subcommand."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

# Importing the CLI module also wires the scorer registry.
import mangomas.cli.main as cli_main
from mangomas.agents import ChatAgent
from mangomas.cli import _runtime as cli_runtime
from mangomas.cli.commands._eval_config import _provenance_labels, _seed_judge_options
from mangomas.cli.main import app
from mangomas.config import (
    LLM_PROFILES_EXTRAS_KEY,
    AgentSettings,
    EvalSettings,
    LLMProfileSettings,
    Settings,
    get_settings,
)
from mangomas.core import AgentContext, Orchestrator
from mangomas.errors import ConfigError
from mangomas.eval import Sink
from mangomas.eval.runner import EvalReport
from mangomas.eval.scorers.llm_judge import judge_prompt_sha256
from mangomas.utils import clock as cli_main_clock
from tests._seam_guards import forbid_real_orchestrator
from tests.constants import (
    DEFAULT_AGENT_NAME,
    EVAL_COST_CONTROLLED_DATASET_FILENAME,
    EVAL_COST_GATE_ABOVE_UNIT_USD,
    EVAL_COST_GATE_TINY_USD,
    EVAL_COST_SCORER_NAME,
    EVAL_COST_USD_METADATA_KEY,
    EVAL_GATE_EXIT_CODE,
    EVAL_MAX_MEAN_COST_USD_ENV,
    EVAL_SINK_JSON_FILE,
    EVAL_THRESHOLD_LENIENT,
    EVAL_THRESHOLD_STRICT,
    EXIT_CONFIG_ERROR,
    EXIT_RUNTIME_ERROR,
    STUB_REPLY,
)
from tests.fakes import FakeLLM, FakeRepository, FakeSink


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
    forbid_real_orchestrator(monkeypatch)
    monkeypatch.setattr(cli_runtime, "_build", lambda: eval_orchestrator)


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


def test_eval_cli_bad_scorer_option_exits_2_before_any_row_runs(
    fixtures_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D3 regression, end-to-end: an option-level config error must abort the
    run with exit code 2 (config), not surface as every row erroring with
    exit code 1 (runtime). A local, concretely-typed ``FakeLLM`` (rather than
    the shared ``eval_orchestrator`` fixture, whose ``AgentContext.llm`` is
    protocol-typed) recording zero calls proves the dataset loop never started.
    """
    fake_llm = FakeLLM()
    orch = Orchestrator(AgentContext(llm=fake_llm, repo=FakeRepository()))
    orch.register(ChatAgent())
    monkeypatch.setattr(cli_runtime, "_build", lambda: orch)
    monkeypatch.setenv("MANGOMAS_EVAL__SCORER_OPTIONS", '{"required_keys": "not-a-list"}')
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "mixed.jsonl"),
            "--scorer",
            "json_keys",
        ],
    )
    assert result.exit_code == 2, result.stdout
    assert "required_keys" in result.stdout + result.stderr
    assert fake_llm.calls == []


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


def test_eval_cli_regression_gate_fails_exit_3(fixtures_dir: Path, tmp_path: Path) -> None:
    """A passing baseline + a regressing echo target trips the regression gate."""
    runner = CliRunner()
    baseline = tmp_path / "baseline.json"
    # 1. Passing baseline via the agent target (FakeLLM echoes STUB_REPLY).
    first = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "all_pass.jsonl"),
            "--scorer",
            "exact_match",
            "--output-json",
            str(baseline),
        ],
    )
    assert first.exit_code == 0, first.stdout
    # 2. Re-run with a regressing echo target gated against the baseline.
    second = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "all_pass.jsonl"),
            "--scorer",
            "exact_match",
            "--target",
            "echo",
            "--baseline",
            str(baseline),
            "--max-mean-score-drop",
            "0.0",
        ],
    )
    assert second.exit_code == EVAL_GATE_EXIT_CODE, second.stdout
    assert "mean_score dropped" in second.stdout


def test_eval_cli_missing_baseline_exits_2(fixtures_dir: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "all_pass.jsonl"),
            "--scorer",
            "exact_match",
            "--baseline",
            str(tmp_path / "does-not-exist.json"),
        ],
    )
    assert result.exit_code == 2
    assert "Baseline report not found" in result.stdout + result.stderr


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


def test_eval_cli_cost_gate_failure(
    fixtures_dir: Path,
) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / EVAL_COST_CONTROLLED_DATASET_FILENAME),
            "--scorer",
            EVAL_COST_SCORER_NAME,
            "--target",
            "echo",
            "--max-mean-cost-usd",
            str(EVAL_COST_GATE_TINY_USD),
        ],
    )
    assert result.exit_code == EVAL_GATE_EXIT_CODE
    combined = result.stdout + result.stderr
    assert "mean_cost_usd" in combined
    assert "gate=FAIL" in combined


def test_eval_cli_cost_gate_from_settings(
    fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(EVAL_MAX_MEAN_COST_USD_ENV, str(EVAL_COST_GATE_TINY_USD))
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / EVAL_COST_CONTROLLED_DATASET_FILENAME),
            "--scorer",
            EVAL_COST_SCORER_NAME,
            "--target",
            "echo",
        ],
    )
    assert result.exit_code == EVAL_GATE_EXIT_CODE
    assert "gate=FAIL" in (result.stdout + result.stderr)


def test_eval_cli_no_gate_overrides_cost_threshold(
    fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(EVAL_MAX_MEAN_COST_USD_ENV, str(EVAL_COST_GATE_TINY_USD))
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / EVAL_COST_CONTROLLED_DATASET_FILENAME),
            "--scorer",
            EVAL_COST_SCORER_NAME,
            "--target",
            "echo",
            "--no-gate",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "gate=" not in result.stdout


def test_eval_cli_cost_threshold_above_unit_interval_passes(fixtures_dir: Path) -> None:
    """USD is not a unit score — 2.5 must not be rejected as out of [0, 1]."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / EVAL_COST_CONTROLLED_DATASET_FILENAME),
            "--scorer",
            EVAL_COST_SCORER_NAME,
            "--target",
            "echo",
            "--max-mean-cost-usd",
            str(EVAL_COST_GATE_ABOVE_UNIT_USD),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "gate=PASS" in result.stdout


def test_eval_cli_cost_gate_failure_still_writes_artifacts(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    out = tmp_path / "report.json"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / EVAL_COST_CONTROLLED_DATASET_FILENAME),
            "--scorer",
            EVAL_COST_SCORER_NAME,
            "--target",
            "echo",
            "--max-mean-cost-usd",
            str(EVAL_COST_GATE_TINY_USD),
            "--output-json",
            str(out),
        ],
    )
    assert result.exit_code == EVAL_GATE_EXIT_CODE
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["gate"]["passed"] is False
    assert payload["mean_cost_usd"] is not None
    assert payload["rows"][0]["metadata"][EVAL_COST_USD_METADATA_KEY] is not None


def test_eval_cli_cost_threshold_without_cost_scorer_exits_0(fixtures_dir: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "all_pass.jsonl"),
            "--scorer",
            "exact_match",
            "--max-mean-cost-usd",
            str(EVAL_COST_GATE_TINY_USD),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "gate=PASS" in result.stdout


def test_eval_cli_negative_cost_threshold_exits_2(fixtures_dir: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / EVAL_COST_CONTROLLED_DATASET_FILENAME),
            "--scorer",
            EVAL_COST_SCORER_NAME,
            "--target",
            "echo",
            "--max-mean-cost-usd",
            "-1",
        ],
    )
    assert result.exit_code == 2
    combined = result.stdout + result.stderr
    assert "max-mean-cost-usd" in combined
    assert "must be a finite number >= 0.0" in combined


@pytest.mark.parametrize("token", ["nan", "inf"])
def test_eval_cli_non_finite_cost_threshold_exits_2(fixtures_dir: Path, token: str) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / EVAL_COST_CONTROLLED_DATASET_FILENAME),
            "--scorer",
            EVAL_COST_SCORER_NAME,
            "--target",
            "echo",
            "--max-mean-cost-usd",
            token,
        ],
    )
    assert result.exit_code == 2
    combined = result.stdout + result.stderr
    assert "max-mean-cost-usd" in combined
    assert "finite number" in combined


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


def test_eval_verbose_requests_debug_logging(
    fixtures_dir: Path, cli_logging_calls: list[dict[str, object]]
) -> None:
    """`eval --verbose` asks for DEBUG logging — see the workflow twin."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "all_pass.jsonl"),
            "--scorer",
            "exact_match",
            "--verbose",
        ],
    )
    assert result.exit_code == 0
    assert cli_logging_calls == [{"verbose": True}]


def test_eval_cli_sink_failure_exits_runtime_code(fixtures_dir: Path, tmp_path: Path) -> None:
    """A sink that raises is exit 1, and the run still completes.

    `test_emit_sinks_isolates_failures` above proves `_emit_sinks` *returns* the
    exception; nothing proved the CLI acted on it. The failure is produced by a
    real `json_file` sink pointed at a path whose parent is a regular file —
    `mkdir(parents=True)` cannot create a directory there — rather than by
    registering a throwing stub, so this exercises the same sink an operator
    would have typo'd their way into.
    """
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "all_pass.jsonl"),
            "--scorer",
            "exact_match",
            "--output-json",
            str(blocker / "report.json"),
        ],
    )

    assert result.exit_code == EXIT_RUNTIME_ERROR
    assert "sink error" in (result.stdout + result.stderr).lower()


def test_output_json_overrides_a_configured_json_file_path(
    fixtures_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI flag beats settings when `json_file` is *already* configured.

    The documented precedence is ``--output-json`` > ``sink_options[json_file]
    [path]``, and the branch where the sink is already in `sinks` (so
    `--output-json` overrides rather than injects) had no test — it is the one
    partial branch `_eval_config.py` reports. Asserting the configured path
    stays *empty* is the half that matters: writing both files would satisfy a
    naive "the override landed" check.
    """
    configured = tmp_path / "from-settings.json"
    override = tmp_path / "from-flag.json"
    monkeypatch.setenv("MANGOMAS_EVAL__SINKS", json.dumps([EVAL_SINK_JSON_FILE]))
    monkeypatch.setenv(
        "MANGOMAS_EVAL__SINK_OPTIONS",
        json.dumps({EVAL_SINK_JSON_FILE: {"path": str(configured)}}),
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "all_pass.jsonl"),
            "--scorer",
            "exact_match",
            "--output-json",
            str(override),
        ],
    )

    assert result.exit_code == 0
    assert override.is_file(), "--output-json path was not written"
    assert not configured.exists(), "the settings path was written despite the CLI override"


def test_eval_cli_malformed_baseline_exits_2_before_the_run(
    fixtures_dir: Path, tmp_path: Path, eval_orchestrator: Orchestrator
) -> None:
    """The baseline used to be only ``is_file()``-checked up front and parsed
    *after* the paid run (exit 1, no sink output). It is now parsed before the
    orchestrator is built: exit 2 and zero LLM calls."""
    runner = CliRunner()
    bad = tmp_path / "baseline.json"
    bad.write_text("{not json", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "all_pass.jsonl"),
            "--scorer",
            "exact_match",
            "--baseline",
            str(bad),
        ],
    )
    assert result.exit_code == 2, result.stdout + result.stderr
    assert "Malformed baseline report" in result.stdout + result.stderr
    assert eval_orchestrator.context.llm.calls == []  # type: ignore[attr-defined]


@pytest.mark.parametrize("flag_value", ["nan", "-0.5", "1.5"])
def test_eval_cli_rejects_out_of_range_regression_drop(
    fixtures_dir: Path, tmp_path: Path, flag_value: str
) -> None:
    """CLI ``--max-*-drop`` values bypassed the settings validator: ``nan`` made
    the regression gate unfailable and a negative value failed every run."""
    runner = CliRunner()
    baseline = tmp_path / "baseline.json"
    first = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "all_pass.jsonl"),
            "--scorer",
            "exact_match",
            "--output-json",
            str(baseline),
        ],
    )
    assert first.exit_code == 0, first.stdout
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "all_pass.jsonl"),
            "--scorer",
            "exact_match",
            "--baseline",
            str(baseline),
            "--max-mean-score-drop",
            flag_value,
        ],
    )
    assert result.exit_code == 2, result.stdout + result.stderr
    assert "max-mean-score-drop" in result.stdout + result.stderr


# ── repeatable -s (spec-0045 B5) ──────────────────────────────────────────────


def test_eval_cli_repeated_scorer_builds_a_composite(fixtures_dir: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--dataset",
            str(fixtures_dir / "all_pass.jsonl"),
            "-s",
            "exact_match",
            "-s",
            "contains",
            "--agent",
            "chat",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "scorer=composite" in result.stdout
    assert "passed=2" in result.stdout


def test_eval_cli_single_scorer_is_unchanged(fixtures_dir: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["eval", "--dataset", str(fixtures_dir / "all_pass.jsonl"), "-s", "contains", "-a", "chat"],
    )
    assert result.exit_code == 0, result.stdout
    assert "scorer=contains" in result.stdout


def test_eval_cli_report_carries_provenance_labels(fixtures_dir: Path, tmp_path: Path) -> None:
    """The CLI adds what the runner cannot see: provider, model, option digests (spec-0045 B6)."""
    out = tmp_path / "report.json"
    result = CliRunner().invoke(
        app,
        [
            "eval",
            "-d",
            str(fixtures_dir / "all_pass.jsonl"),
            "-s",
            "exact_match",
            "-a",
            "chat",
            "-o",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.stdout
    run = json.loads(out.read_text(encoding="utf-8"))["run"]
    assert run["labels"]["llm_provider"] and run["labels"]["llm_model"]
    assert run["labels"]["scorers"] == ["exact_match"]
    assert run["labels"]["scorer_options_sha256"].startswith("sha256:")
    assert run["dataset_sha256"].startswith("sha256:")


# --- provenance labels follow the client the run dispatches through ---------
#
# Copilot review on PR #124: ``llm_provider`` / ``llm_model`` used to be read
# from the base ``MANGOMAS_LLM__*`` block even when the ``agent`` target had
# selected a profile, so a cloud-vs-local baseline pair labelled both runs as
# the local model. The labels now mirror the dispatch precedence
# (profile > per-agent ``MODEL_OVERRIDE`` > shared client).

_PROFILE_NAME = "cloud"
_PROFILE_MODEL = "profile-model"
_OVERRIDE_MODEL = "override-model"


def _settings_with(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_provenance_labels_follow_the_selected_profile() -> None:
    settings = _settings_with(
        llm_profiles={_PROFILE_NAME: LLMProfileSettings(provider="vertex", model=_PROFILE_MODEL)},
        eval=EvalSettings(target_options={"agent": {"llm_profile": _PROFILE_NAME}}),
    )
    labels = _provenance_labels(settings, "agent", ["exact_match"])
    assert (labels["llm_provider"], labels["llm_model"]) == ("vertex", _PROFILE_MODEL)
    assert labels["llm_profile"] == _PROFILE_NAME


def test_provenance_labels_follow_the_agent_model_override() -> None:
    settings = _settings_with(
        agents={DEFAULT_AGENT_NAME: AgentSettings(model_override=_OVERRIDE_MODEL)},
        eval=EvalSettings(agent="planner"),
    )
    by_flag = _provenance_labels(settings, "agent", ["exact_match"], agent_flag=DEFAULT_AGENT_NAME)
    assert (by_flag["llm_provider"], by_flag["llm_model"]) == (
        settings.llm.provider,
        _OVERRIDE_MODEL,
    )
    assert by_flag["llm_profile"] is None
    # Without the flag the settings default agent (planner) has no override.
    default = _provenance_labels(settings, "agent", ["exact_match"])
    assert default["llm_model"] == settings.llm.model
    # ``target_options["agent"]["agent"]`` is the middle of the precedence.
    settings = _settings_with(
        agents={DEFAULT_AGENT_NAME: AgentSettings(model_override=_OVERRIDE_MODEL)},
        eval=EvalSettings(target_options={"agent": {"agent": DEFAULT_AGENT_NAME}}),
    )
    assert _provenance_labels(settings, "agent", ["exact_match"])["llm_model"] == _OVERRIDE_MODEL


def test_provenance_labels_fall_back_to_the_shared_client() -> None:
    settings = _settings_with(
        llm_profiles={_PROFILE_NAME: LLMProfileSettings(provider="vertex", model=_PROFILE_MODEL)},
        agents={DEFAULT_AGENT_NAME: AgentSettings(model_override=_OVERRIDE_MODEL)},
        eval=EvalSettings(target_options={"agent": {"llm_profile": _PROFILE_NAME}}),
    )
    # A multi-agent target dispatches through the shared client; no profile is stamped.
    labels = _provenance_labels(
        settings, "pipeline", ["exact_match"], agent_flag=DEFAULT_AGENT_NAME
    )
    assert (labels["llm_provider"], labels["llm_model"]) == (
        settings.llm.provider,
        settings.llm.model,
    )
    assert labels["llm_profile"] is None
    # An unknown profile name also falls back to the shared client.
    unknown = _settings_with(eval=EvalSettings(target_options={"agent": {"llm_profile": "nope"}}))
    assert _provenance_labels(unknown, "agent", ["exact_match"])["llm_profile"] is None
    assert _provenance_labels(unknown, "agent", ["exact_match"])["llm_model"] == unknown.llm.model


# --- --trials / --min-pass-pow-k (spec-0046) --------------------------------

_FLAKY_K = 2


def _flaky_orchestrator() -> Orchestrator:
    """Row p1 passes then fails; p2 passes twice → pass_pow_k 0.5, trial_pass_rate 0.75."""
    orch = Orchestrator(
        AgentContext(llm=FakeLLM(replies=[STUB_REPLY, "wrong"]), repo=FakeRepository())
    )
    orch.register(ChatAgent())
    return orch


def test_eval_cli_trials_collapse_rows_and_report_pass_pow_k(
    fixtures_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli_runtime, "_build", _flaky_orchestrator)
    out = tmp_path / "report.json"
    result = CliRunner().invoke(
        app,
        [
            "eval",
            "-d",
            str(fixtures_dir / "all_pass.jsonl"),
            "-s",
            "exact_match",
            "-a",
            "chat",
            "--trials",
            str(_FLAKY_K),
            "-o",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert f"trials={_FLAKY_K} pass_pow_k=0.500 trial_pass_rate=0.750" in result.stdout
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert (payload["trials"], payload["pass_pow_k"], payload["trial_pass_rate"]) == (
        _FLAKY_K,
        0.5,
        0.75,
    )
    assert payload["rows"][0]["metadata"]["trials"]["count"] == _FLAKY_K


def test_eval_cli_min_pass_pow_k_gates_a_multi_trial_run(
    fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli_runtime, "_build", _flaky_orchestrator)
    result = CliRunner().invoke(
        app,
        [
            "eval",
            "-d",
            str(fixtures_dir / "all_pass.jsonl"),
            "-s",
            "exact_match",
            "-a",
            "chat",
            "-k",
            str(_FLAKY_K),
            "--min-pass-pow-k",
            "0.9",
        ],
    )
    assert result.exit_code == EVAL_GATE_EXIT_CODE
    assert "pass_pow_k 0.500 < min_pass_pow_k 0.900" in result.stdout


def test_eval_cli_min_pass_pow_k_fails_closed_without_trials(fixtures_dir: Path) -> None:
    """A perfect single-trial run cannot satisfy a reliability threshold."""
    result = CliRunner().invoke(
        app,
        [
            "eval",
            "-d",
            str(fixtures_dir / "all_pass.jsonl"),
            "-s",
            "exact_match",
            "-a",
            "chat",
            "--min-pass-pow-k",
            "0.5",
        ],
    )
    assert result.exit_code == EVAL_GATE_EXIT_CODE
    assert "requires trials >= 2" in result.stdout


def test_eval_cli_trials_env_and_threshold_validation(
    fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MANGOMAS_EVAL__TRIALS", str(_FLAKY_K))
    monkeypatch.setenv("MANGOMAS_EVAL__MIN_PASS_POW_K", "1.0")
    ok = CliRunner().invoke(
        app, ["eval", "-d", str(fixtures_dir / "all_pass.jsonl"), "-s", "exact_match", "-a", "chat"]
    )
    assert ok.exit_code == 0, ok.stdout
    assert f"trials={_FLAKY_K} pass_pow_k=1.000" in ok.stdout and "gate=PASS" in ok.stdout
    bad_k = CliRunner().invoke(
        app,
        [
            "eval",
            "-d",
            str(fixtures_dir / "all_pass.jsonl"),
            "-s",
            "exact_match",
            "-a",
            "chat",
            "--trials",
            "0",
        ],
    )
    assert bad_k.exit_code == EXIT_CONFIG_ERROR and "trials must be >= 1" in bad_k.output
    bad_threshold = CliRunner().invoke(
        app,
        [
            "eval",
            "-d",
            str(fixtures_dir / "all_pass.jsonl"),
            "-s",
            "exact_match",
            "-a",
            "chat",
            "--min-pass-pow-k",
            "1.5",
        ],
    )
    assert bad_threshold.exit_code == EXIT_CONFIG_ERROR and "min-pass-pow-k" in bad_threshold.output


# --- judge calibration seeding (spec-0047) ----------------------------------

_JUDGE_REPLY = '{"score": 0.2, "rationale": "weak"}'
_JUDGE_MODEL_ENV = "MANGOMAS_LLM__MODEL"
_JUDGE_RECORD_ENV = "MANGOMAS_EVAL__JUDGE_CALIBRATION_PATH"


def _judge_orchestrator() -> Orchestrator:
    orch = Orchestrator(AgentContext(llm=FakeLLM(reply=_JUDGE_REPLY), repo=FakeRepository()))
    orch.register(ChatAgent())
    return orch


def _write_calibration(path: Path, model: str, **overrides: object) -> Path:
    record: dict[str, object] = {
        "judge_model": model,
        "measured_at": "2026-11-01",
        "sample_size": 300,
        "cohens_kappa": 0.9,
        "self_preference": "measured_unbiased",
        "prompt_sha256": judge_prompt_sha256(),
    }
    record.update(overrides)
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def _judge_eval(*extra: str) -> list[str]:
    return [
        "eval",
        "-d",
        "tests/eval/fixtures/all_pass.jsonl",
        "-s",
        "llm_judge",
        "-a",
        "chat",
        *extra,
    ]


def test_eval_cli_uncalibrated_judge_is_reported_not_gated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli_runtime, "_build", _judge_orchestrator)
    out = tmp_path / "report.json"
    result = CliRunner().invoke(app, _judge_eval("--min-mean-score", "0.9", "-o", str(out)))
    assert result.exit_code == 0, result.stdout
    assert "judge=llm_judge uncalibrated (no calibration record" in result.stdout
    assert "gate=PASS (note: llm_judge uncalibrated" in result.stdout
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["judge"]["calibrated"] is False
    assert payload["gate"]["judge_excluded"] is True
    assert payload["mean_score"] == pytest.approx(0.2), "the scores are still reported"


def test_eval_cli_calibrated_judge_is_gated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli_runtime, "_build", _judge_orchestrator)
    monkeypatch.setenv(_JUDGE_MODEL_ENV, "judge-under-test")
    record = _write_calibration(tmp_path / "cal.json", "judge-under-test")
    monkeypatch.setenv(_JUDGE_RECORD_ENV, str(record))
    frozen = __import__("datetime").datetime(2026, 11, 10, tzinfo=__import__("datetime").UTC)
    monkeypatch.setattr(cli_main_clock, "now", lambda: frozen)
    result = CliRunner().invoke(app, _judge_eval("--min-mean-score", "0.9"))
    assert result.exit_code == EVAL_GATE_EXIT_CODE, result.stdout
    assert "judge=llm_judge calibrated (kappa=0.90" in result.stdout
    assert "self-preference: measured_unbiased" in result.stdout
    assert "gate=FAIL (mean_score" in result.stdout


def test_eval_cli_record_for_another_model_stays_uncalibrated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli_runtime, "_build", _judge_orchestrator)
    monkeypatch.setenv(_JUDGE_MODEL_ENV, "judge-under-test")
    monkeypatch.setenv(_JUDGE_RECORD_ENV, str(_write_calibration(tmp_path / "cal.json", "other")))
    result = CliRunner().invoke(app, _judge_eval("--min-mean-score", "0.9"))
    assert result.exit_code == 0, result.stdout
    assert "record is for model 'other'" in result.stdout


def test_eval_cli_malformed_calibration_record_is_a_config_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    broken = tmp_path / "cal.json"
    broken.write_text("{nope", encoding="utf-8")
    monkeypatch.setenv(_JUDGE_RECORD_ENV, str(broken))
    result = CliRunner().invoke(app, _judge_eval())
    assert result.exit_code == EXIT_CONFIG_ERROR
    assert "calibration record" in result.output


def test_eval_cli_composite_with_a_judge_child_is_demoted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_runtime, "_build", _judge_orchestrator)
    result = CliRunner().invoke(
        app,
        [
            "eval",
            "-d",
            "tests/eval/fixtures/all_pass.jsonl",
            "-s",
            "contains",
            "-s",
            "llm_judge",
            "-a",
            "chat",
            "--min-pass-rate",
            "0.9",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "judge=llm_judge (child of composite) uncalibrated" in result.stdout


def test_seed_judge_options_only_fills_gaps() -> None:
    cfg = EvalSettings(judge_calibration_path="cal.json", judge_min_sample_size=50)
    flat = _seed_judge_options(
        ["llm_judge"],
        {"threshold": 0.5, "min_kappa": 0.7},
        cfg=cfg,
        base_model="m",
        profile_models={},
    )
    assert flat["threshold"] == 0.5 and flat["min_kappa"] == 0.7, "explicit options win"
    assert flat["calibration_path"] == "cal.json"
    assert (flat["judge_model"], flat["min_sample_size"]) == ("m", 50)
    assert flat["judge_profile"] is None
    nested = _seed_judge_options(
        ["contains", "llm_judge"],
        {"llm_judge": {"threshold": 0.6}, "contains": {}},
        cfg=cfg,
        base_model="m",
        profile_models={},
    )
    child = nested["llm_judge"]
    assert isinstance(child, dict) and child["threshold"] == 0.6 and child["judge_model"] == "m"
    assert nested["contains"] == {}
    untouched = _seed_judge_options(
        ["exact_match"], {"x": 1}, cfg=cfg, base_model="m", profile_models={}
    )
    assert untouched == {"x": 1}
    replaced = _seed_judge_options(
        ["contains", "llm_judge"], {"llm_judge": "bad"}, cfg=cfg, base_model="m", profile_models={}
    )
    assert isinstance(replaced["llm_judge"], dict)


def test_seed_judge_options_resolves_the_judging_model_from_the_profile() -> None:
    """spec-0048: with a judge profile the record must cover the profile's model."""
    via_settings = _seed_judge_options(
        ["llm_judge"],
        {},
        cfg=EvalSettings(judge_profile="cloud"),
        base_model="local",
        profile_models={"cloud": "cloud-model"},
    )
    assert (via_settings["judge_profile"], via_settings["judge_model"]) == ("cloud", "cloud-model")
    explicit = _seed_judge_options(
        ["llm_judge"],
        {"judge_profile": "cloud"},
        cfg=EvalSettings(),
        base_model="local",
        profile_models={"cloud": "cloud-model"},
    )
    assert explicit["judge_model"] == "cloud-model"
    with pytest.raises(ConfigError, match="judge_profile 'nope' is not a configured LLM profile"):
        _seed_judge_options(
            ["llm_judge"],
            {},
            cfg=EvalSettings(judge_profile="nope"),
            base_model="local",
            profile_models={"cloud": "cloud-model"},
        )


def test_eval_cli_judge_profile_grades_through_the_profile_and_its_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile_llm = FakeLLM(reply='{"score": 0.95, "rationale": "solid"}')
    shared_llm = FakeLLM(reply=_JUDGE_REPLY)

    def _build() -> Orchestrator:
        orch = Orchestrator(AgentContext(llm=shared_llm, repo=FakeRepository()))
        orch.register(ChatAgent())
        orch.context.extras[LLM_PROFILES_EXTRAS_KEY] = {"cloud": profile_llm}
        return orch

    monkeypatch.setattr(cli_runtime, "_build", _build)
    monkeypatch.setenv("MANGOMAS_LLM_PROFILES__CLOUD__PROVIDER", "lmstudio")
    monkeypatch.setenv("MANGOMAS_LLM_PROFILES__CLOUD__MODEL", "cloud-judge")
    monkeypatch.setenv("MANGOMAS_EVAL__JUDGE_PROFILE", "cloud")
    record = _write_calibration(tmp_path / "cal.json", "cloud-judge")
    monkeypatch.setenv(_JUDGE_RECORD_ENV, str(record))
    frozen = __import__("datetime").datetime(2026, 11, 10, tzinfo=__import__("datetime").UTC)
    monkeypatch.setattr(cli_main_clock, "now", lambda: frozen)
    result = CliRunner().invoke(app, _judge_eval("--min-mean-score", "0.9"))
    assert result.exit_code == 0, result.stdout
    assert "judge=llm_judge via profile cloud calibrated (kappa=0.90" in result.stdout
    assert "gate=PASS" in result.stdout
    assert profile_llm.calls, "the profile client judged"
    assert len(shared_llm.calls) == 2, "the shared client only answered the agent rows"


def test_eval_cli_unknown_judge_profile_is_a_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_EVAL__JUDGE_PROFILE", "nope")
    result = CliRunner().invoke(app, _judge_eval())
    assert result.exit_code == EXIT_CONFIG_ERROR
    assert "judge_profile 'nope' is not a configured LLM profile" in result.output
