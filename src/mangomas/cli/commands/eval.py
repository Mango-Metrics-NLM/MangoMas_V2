"""The `mangomas eval` command: run, gate, emit.

Holds the four `mangomas.eval.*` side-effect imports that seed the scorer,
sink, source and target registries. They lived at the top of `cli/main.py` and
must stay reachable from it: `tests/eval/test_cli_eval.py` relies on importing
the CLI being enough to make the built-in scorers resolvable.

Configuration precedence lives next door in `_eval_config`; this module is the
part that builds an orchestrator and does I/O.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import typer

import mangomas.eval.scorers  # registers built-in scorers (side-effect import)
import mangomas.eval.sinks
import mangomas.eval.sources
import mangomas.eval.targets  # noqa: F401 — registers built-in targets
from mangomas.cli import _runtime
from mangomas.cli.commands._eval_config import (
    _build_dataset_source,
    _build_sinks,
    _build_target,
    _resolve_gating,
    _resolve_regression,
)
from mangomas.cli.exit_codes import EVAL_GATE_EXIT_CODE, EXIT_CONFIG_ERROR, EXIT_RUNTIME_ERROR
from mangomas.config import get_settings
from mangomas.errors import ConfigError, MangomasError
from mangomas.eval import (
    EvalReport,
    EvalRunner,
    diff_reports,
    ensure_eval_plugins,
    evaluate_gate,
    evaluate_regression_gate,
    load_baseline,
    merge_gate_results,
    scorer_registry,
)

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.eval import GateResult, Sink

# Pinned to the pre-decomposition name rather than `__name__`. This logger's
# only record is the per-sink failure in `_emit_sinks`, which operators filter
# on; a refactor promising no behaviour change must not rename a log field.
logger = logging.getLogger("mangomas.cli.main")


async def _emit_sinks(
    sinks: list[Sink],
    report: EvalReport,
    gate_result: GateResult | None,
) -> BaseException | None:
    """Emit *report* to every sink under per-sink fault isolation.

    A failure in one sink is logged and does not prevent the others; the first
    exception is returned so the caller can surface a non-zero exit *after*
    every sink has been attempted (so partial artifacts still land).
    """
    first_exc: BaseException | None = None
    for sink in sinks:
        try:
            await sink.emit(report, gate_result=gate_result)
        except Exception as exc:
            logger.exception("Eval sink %r failed", sink.name)
            first_exc = first_exc or exc
    return first_exc


async def _evaluate_run_gates(
    report: EvalReport,
    *,
    gating_engaged: bool,
    min_mean_score: float | None,
    min_pass_rate: float | None,
    fail_on_error: bool,
    baseline_path: str | None,
    max_mean_score_drop: float | None,
    max_pass_rate_drop: float | None,
    allow_new_failures: bool,
) -> GateResult | None:
    """Compute the merged threshold + regression gate verdict for *report*.

    Returns ``None`` when neither gate is engaged. The baseline is loaded and
    diffed here (its existence was validated up front for exit-2 semantics).
    """
    threshold_gate = (
        evaluate_gate(
            report,
            min_mean_score=min_mean_score,
            min_pass_rate=min_pass_rate,
            fail_on_error=fail_on_error,
        )
        if gating_engaged
        else None
    )
    regression_gate = None
    if baseline_path:
        diff = diff_reports(await load_baseline(baseline_path), report)
        regression_gate = evaluate_regression_gate(
            diff,
            max_mean_score_drop=max_mean_score_drop,
            max_pass_rate_drop=max_pass_rate_drop,
            allow_new_failures=allow_new_failures,
        )
    return merge_gate_results([threshold_gate, regression_gate])


def _finish_eval(
    *,
    output_json: str | None,
    sink_error: BaseException | None,
    gate_result: GateResult | None,
) -> None:
    """Surface the run outcome: sink error (exit 1), confirmation, gate (exit 3)."""
    if sink_error is not None:
        typer.echo(f"Eval sink error: {sink_error}", err=True)
        raise typer.Exit(code=EXIT_RUNTIME_ERROR)
    # Preserve the pre-sink-refactor confirmation line so existing
    # ``--output-json`` scripts still see "Report written to ...".
    if output_json:
        typer.echo(f"Report written to {output_json}")
    if gate_result is not None and not gate_result.passed:
        raise typer.Exit(code=EVAL_GATE_EXIT_CODE)


def eval_cmd(
    dataset_path: str | None = typer.Option(
        None,
        "--dataset",
        "-d",
        help="JSONL dataset path (jsonl source); falls back to MANGOMAS_EVAL__DATASET_PATH.",
    ),
    dataset_source: str | None = typer.Option(
        None,
        "--dataset-source",
        help="Dataset source: jsonl|inline|langfuse (default from MANGOMAS_EVAL__DATASET_SOURCE).",
    ),
    scorer: str | None = typer.Option(
        None,
        "--scorer",
        "-s",
        help="Scorer name (default from MANGOMAS_EVAL__SCORER).",
    ),
    agent: str | None = typer.Option(
        None,
        "--agent",
        "-a",
        help="Agent name for the 'agent' target (default from MANGOMAS_EVAL__AGENT).",
    ),
    target: str | None = typer.Option(
        None,
        "--target",
        "-t",
        help="Target name: agent|pipeline|fan_out|echo (default from MANGOMAS_EVAL__TARGET).",
    ),
    parallelism: int | None = typer.Option(
        None,
        "--parallelism",
        "-p",
        help="Max concurrent rows (default from MANGOMAS_EVAL__PARALLELISM).",
    ),
    fail_fast: bool | None = typer.Option(
        None,
        "--fail-fast/--no-fail-fast",
        help="Cancel remaining rows after the first non-pass.",
    ),
    output_json: str | None = typer.Option(
        None,
        "--output-json",
        "-o",
        help="If set, write a JSON report to this path (injects the json_file sink).",
    ),
    gate: bool | None = typer.Option(
        None,
        "--gate/--no-gate",
        help="Enable/disable the quality gate (default from MANGOMAS_EVAL__GATE_ENABLED).",
    ),
    min_mean_score: float | None = typer.Option(
        None,
        "--min-mean-score",
        help="Fail (exit 3) if mean_score < this threshold.",
    ),
    min_pass_rate: float | None = typer.Option(
        None,
        "--min-pass-rate",
        help="Fail (exit 3) if pass_rate < this threshold.",
    ),
    fail_on_error: bool | None = typer.Option(
        None,
        "--fail-on-error/--no-fail-on-error",
        help="Fail the gate (exit 3) if any row errored.",
    ),
    baseline: str | None = typer.Option(
        None,
        "--baseline",
        help="Baseline report JSON to diff against (default from MANGOMAS_EVAL__BASELINE_PATH).",
    ),
    max_mean_score_drop: float | None = typer.Option(
        None,
        "--max-mean-score-drop",
        help="Fail (exit 3) if mean_score drops more than this vs the baseline.",
    ),
    max_pass_rate_drop: float | None = typer.Option(
        None,
        "--max-pass-rate-drop",
        help="Fail (exit 3) if pass_rate drops more than this vs the baseline.",
    ),
    allow_new_failures: bool | None = typer.Option(
        None,
        "--allow-new-failures/--no-allow-new-failures",
        help="Fail the gate (exit 3) on rows that passed in the baseline but fail now.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable DEBUG logging"),
) -> None:
    """Run the configured scorer over a JSONL dataset, emit to sinks, and gate."""
    _runtime.configure_cli_logging(verbose=verbose)

    settings = get_settings()
    cfg = settings.eval
    # Layer in any entry-point scorer/sink/target/source plugins (no-op unless enabled).
    ensure_eval_plugins(settings)

    scorer_name = scorer or cfg.scorer
    target_name = target or cfg.target
    source_name = dataset_source or cfg.dataset_source
    effective_parallelism = parallelism if parallelism is not None else cfg.parallelism
    effective_fail_fast = fail_fast if fail_fast is not None else cfg.fail_fast

    gating_engaged, eff_min_mean, eff_min_pass, eff_fail_on_error = _resolve_gating(
        gate=gate,
        min_mean_score=min_mean_score,
        min_pass_rate=min_pass_rate,
        fail_on_error=fail_on_error,
        cfg=cfg,
    )

    effective_baseline, eff_max_mean_drop, eff_max_pass_drop, eff_allow_new = _resolve_regression(
        baseline=baseline,
        max_mean_score_drop=max_mean_score_drop,
        max_pass_rate_drop=max_pass_rate_drop,
        allow_new_failures=allow_new_failures,
        cfg=cfg,
    )

    # Build scorer + sinks up front so misconfiguration fails fast (exit 2)
    # before a (potentially long) eval run.
    try:
        scorer_factory = scorer_registry.get(scorer_name)
        scorer_instance = scorer_factory(dict(cfg.scorer_options))
        sinks = _build_sinks(list(cfg.sinks), cfg.sink_options, output_json)
        target_instance = _build_target(target_name, cfg.target_options, agent, cfg.agent)
        source_instance = _build_dataset_source(
            source_name, cfg.dataset_source_options, dataset_path, cfg.dataset_path
        )
        # Validate the baseline exists up front so a missing file is exit 2
        # (config), not exit 1 (runtime). Parsing happens during the run.
        if effective_baseline and not Path(effective_baseline).is_file():
            raise ConfigError(f"Baseline report not found: {effective_baseline}")
    except MangomasError as exc:
        detail = f" ({exc.detail})" if exc.detail else ""
        typer.echo(f"Eval configuration error: {exc}{detail}", err=True)
        raise typer.Exit(code=EXIT_CONFIG_ERROR) from exc

    orch = _runtime._build()
    runner = EvalRunner(
        orch,
        scorer_instance,
        parallelism=effective_parallelism,
        fail_fast=effective_fail_fast,
    )

    async def _run_eval() -> tuple[EvalReport, GateResult | None, BaseException | None]:
        try:
            dataset = await source_instance.load()
            report = await runner.run(dataset, target=target_instance)
        finally:
            await _runtime._close_orchestrator(orch)
        gate_result = await _evaluate_run_gates(
            report,
            gating_engaged=gating_engaged,
            min_mean_score=eff_min_mean,
            min_pass_rate=eff_min_pass,
            fail_on_error=eff_fail_on_error,
            baseline_path=effective_baseline,
            max_mean_score_drop=eff_max_mean_drop,
            max_pass_rate_drop=eff_max_pass_drop,
            allow_new_failures=eff_allow_new,
        )
        sink_error = await _emit_sinks(sinks, report, gate_result)
        return report, gate_result, sink_error

    try:
        _report, gate_result, sink_error = asyncio.run(_run_eval())
    except MangomasError as exc:
        typer.echo(f"Eval run failed: {exc}", err=True)
        raise typer.Exit(code=EXIT_RUNTIME_ERROR) from exc

    _finish_eval(output_json=output_json, sink_error=sink_error, gate_result=gate_result)
