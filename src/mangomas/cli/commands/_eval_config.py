"""Factory-time resolution of the `eval` command's configuration.

Split out of `eval.py` rather than left beside it: these five helpers are the
CLI-flag-over-settings precedence rules, they touch no orchestrator and run no
eval, and keeping them here is what stops `commands/eval.py` from simply
becoming the largest module in `src/` again after the decomposition.

Every one of them resolves precedence and validates; none of them execute.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from mangomas.cli.exit_codes import EXIT_CONFIG_ERROR
from mangomas.errors import ConfigError
from mangomas.eval import dataset_source_registry, sink_registry, target_registry

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.config import EvalSettings
    from mangomas.eval import DatasetSource, Sink, Target


def _build_sinks(
    sink_names: list[str],
    sink_options: dict[str, dict[str, object]],
    output_json: str | None,
) -> list[Sink]:
    """Resolve sink names to instances, honouring the legacy ``--output-json``.

    ``--output-json`` injects (or overrides the ``path`` of) the ``json_file``
    sink so pre-existing invocations keep producing the same file. CLI flag
    precedence: ``--output-json`` > ``sink_options["json_file"]["path"]``. Any
    *other* configured ``sink_options["json_file"]`` keys are preserved even
    when the sink is injected solely by ``--output-json``.
    Raises :class:`~mangomas.errors.MangomasError` (→ exit 2) on an unknown sink
    or a misconfigured option.
    """
    names = list(sink_names)
    options: dict[str, dict[str, object]] = {n: dict(sink_options.get(n, {})) for n in names}
    if output_json:
        if "json_file" not in names:
            names.append("json_file")
        # Seed from any configured json_file options so injecting the sink via
        # --output-json never silently drops them; only ``path`` is overridden.
        options.setdefault("json_file", dict(sink_options.get("json_file", {})))
        options["json_file"]["path"] = output_json
    return [sink_registry.get(name)(options.get(name, {})) for name in names]


def _build_target(
    target_name: str,
    target_options: dict[str, dict[str, object]],
    agent_flag: str | None,
    default_agent: str,
) -> Target:
    """Resolve a target name to an instance, folding in the agent selection.

    For the ``agent`` target the agent name is sourced with precedence
    ``--agent`` flag > ``target_options["agent"]["agent"]`` > settings default,
    so existing ``--agent X`` invocations keep selecting the agent. Other
    targets read their config straight from ``target_options``. Raises
    :class:`~mangomas.errors.MangomasError` (→ exit 2) on an unknown target or a
    misconfigured option.
    """
    options = dict(target_options.get(target_name, {}))
    if target_name == "agent":
        if agent_flag is not None:
            options["agent"] = agent_flag
        else:
            options.setdefault("agent", default_agent)
    return target_registry.get(target_name)(options)


def _build_dataset_source(
    source_name: str,
    source_options: dict[str, dict[str, object]],
    dataset_flag: str | None,
    default_path: str | None,
) -> DatasetSource:
    """Resolve a dataset source name to an instance, folding in the JSONL path.

    For the ``jsonl`` source the path is sourced with precedence ``--dataset``
    flag > ``dataset_source_options["jsonl"]["path"]`` > settings ``dataset_path``,
    so existing ``--dataset`` invocations keep working. Other sources read their
    config straight from ``dataset_source_options``. Raises
    :class:`~mangomas.errors.MangomasError` (→ exit 2) on an unknown source or a
    missing/misconfigured option (e.g. no JSONL path).
    """
    options = dict(source_options.get(source_name, {}))
    if source_name == "jsonl":
        path = dataset_flag or options.get("path") or default_path
        if not path:
            raise ConfigError(
                "No dataset path provided. Pass --dataset or set MANGOMAS_EVAL__DATASET_PATH."
            )
        options["path"] = path
    return dataset_source_registry.get(source_name)(options)


def _resolve_gating(
    *,
    gate: bool | None,
    min_mean_score: float | None,
    min_pass_rate: float | None,
    fail_on_error: bool | None,
    max_mean_cost_usd: float | None,
    cfg: EvalSettings,
) -> tuple[bool, float | None, float | None, bool, float | None]:
    """Resolve effective gate config (CLI over settings) and validate thresholds.

    Returns ``(gating_engaged, min_mean, min_pass, fail_on_error, max_mean_cost_usd)``.
    An explicit ``--no-gate`` disables gating even when thresholds /
    ``fail_on_error`` / ``max_mean_cost_usd`` are configured via settings/env.
    Raises ``typer.Exit(EXIT_CONFIG_ERROR)`` on an out-of-range unit threshold
    or a negative cost threshold — but only when gating is engaged, since an
    unused threshold should not block a run.
    """
    eff_min_mean = min_mean_score if min_mean_score is not None else cfg.min_mean_score
    eff_min_pass = min_pass_rate if min_pass_rate is not None else cfg.min_pass_rate
    eff_fail_on_error = fail_on_error if fail_on_error is not None else cfg.fail_on_error
    eff_max_mean_cost = (
        max_mean_cost_usd if max_mean_cost_usd is not None else cfg.max_mean_cost_usd
    )
    gate_enabled = gate if gate is not None else cfg.gate_enabled
    if gate is False:
        gating_engaged = False
    else:
        gating_engaged = (
            gate_enabled
            or eff_min_mean is not None
            or eff_min_pass is not None
            or eff_fail_on_error
            or eff_max_mean_cost is not None
        )

    if gating_engaged:
        for label, val in (("min-mean-score", eff_min_mean), ("min-pass-rate", eff_min_pass)):
            if val is not None and not 0.0 <= val <= 1.0:
                typer.echo(
                    f"Eval configuration error: {label} must be in [0.0, 1.0]; got {val}",
                    err=True,
                )
                raise typer.Exit(code=EXIT_CONFIG_ERROR)
        if eff_max_mean_cost is not None and eff_max_mean_cost < 0.0:
            typer.echo(
                "Eval configuration error: max-mean-cost-usd must be >= 0.0; "
                f"got {eff_max_mean_cost}",
                err=True,
            )
            raise typer.Exit(code=EXIT_CONFIG_ERROR)
    return gating_engaged, eff_min_mean, eff_min_pass, eff_fail_on_error, eff_max_mean_cost


def _resolve_regression(
    *,
    baseline: str | None,
    max_mean_score_drop: float | None,
    max_pass_rate_drop: float | None,
    allow_new_failures: bool | None,
    cfg: EvalSettings,
) -> tuple[str | None, float | None, float | None, bool]:
    """Resolve effective regression-gate config (CLI flag over settings)."""
    return (
        baseline or cfg.baseline_path,
        max_mean_score_drop if max_mean_score_drop is not None else cfg.max_mean_score_drop,
        max_pass_rate_drop if max_pass_rate_drop is not None else cfg.max_pass_rate_drop,
        allow_new_failures if allow_new_failures is not None else cfg.allow_new_failures,
    )
