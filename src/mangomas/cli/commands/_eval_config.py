"""Factory-time resolution of the `eval` command's configuration.

Split out of `eval.py` rather than left beside it: these five helpers are the
CLI-flag-over-settings precedence rules, they touch no orchestrator and run no
eval, and keeping them here is what stops `commands/eval.py` from simply
becoming the largest module in `src/` again after the decomposition.

Every one of them resolves precedence and validates; none of them execute.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING

import typer

from mangomas.cli.exit_codes import EXIT_CONFIG_ERROR
from mangomas.errors import ConfigError
from mangomas.eval import dataset_source_registry, scorer_registry, sink_registry, target_registry
from mangomas.eval._options import require_non_negative_float, require_unit_float
from mangomas.eval.provenance import digest_of

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.config import EvalSettings, Settings
    from mangomas.eval import DatasetSource, Scorer, Sink, Target


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


_COMPOSITE_SCORER = "composite"
_JUDGE_SCORER = "llm_judge"


def _seed_judge_options(
    scorer_names: list[str],
    scorer_options: dict[str, object],
    *,
    cfg: EvalSettings,
    base_model: str,
    profile_models: Mapping[str, str],
) -> dict[str, object]:
    """Hand the ``llm_judge`` factory its calibration inputs (spec-0047 / spec-0048).

    The factory stays Settings-free, so the CLI — the one place that holds
    both — seeds ``calibration_path``, ``judge_profile`` and the policy floors
    from ``MANGOMAS_EVAL__JUDGE_*`` into the judge's options: the flat mapping
    for a single scorer, the sub-mapping under ``"llm_judge"`` for a composite
    child. ``setdefault`` only, so an explicit option wins. ``judge_model`` is
    the model that actually grades: the effective ``judge_profile``'s model
    when one is set (unknown profile → :class:`ConfigError`, exit 2), else the
    shared ``MANGOMAS_LLM__MODEL`` — an agent-level profile or
    ``MODEL_OVERRIDE`` never changes which model judges.
    """
    if _JUDGE_SCORER not in scorer_names:
        return dict(scorer_options)
    options = dict(scorer_options)
    if len(scorer_names) == 1:
        target = options
    else:
        nested = options.get(_JUDGE_SCORER)
        target = dict(nested) if isinstance(nested, dict) else {}
        options[_JUDGE_SCORER] = target
    seeds: dict[str, object] = {
        "calibration_path": cfg.judge_calibration_path,
        "judge_profile": cfg.judge_profile,
        "min_sample_size": cfg.judge_min_sample_size,
        "min_kappa": cfg.judge_min_kappa,
        "min_rho": cfg.judge_min_rho,
        "max_age_days": cfg.judge_calibration_max_age_days,
    }
    for key, value in seeds.items():
        target.setdefault(key, value)
    profile = target.get("judge_profile")
    if profile is None:
        target.setdefault("judge_model", base_model)
    elif isinstance(profile, str) and profile in profile_models:
        target.setdefault("judge_model", profile_models[profile])
    else:
        raise ConfigError(
            f"judge_profile {profile!r} is not a configured LLM profile "
            f"(MANGOMAS_LLM_PROFILES__<NAME>); available: {sorted(profile_models)}"
        )
    return options


def _resolve_llm_labels(
    settings: Settings, target_name: str, agent_flag: str | None
) -> tuple[str, str, str | None]:
    """``(provider, model, profile)`` the run will actually use (spec-0045 B6).

    Mirrors the dispatch precedence (spec-0040 / ADR-0028): for the ``agent``
    target a selected ``llm_profile`` names its own provider and model, else an
    agent ``MODEL_OVERRIDE`` swaps the model on the base provider, else the
    shared client. Other targets dispatch several agents, so only the shared
    client is a truthful label for them and no profile is stamped.
    """
    base = (settings.llm.provider, settings.llm.model, None)
    if target_name != "agent":
        return base
    # ``EvalSettings.target_options`` is typed ``dict[str, dict[str, object]]``,
    # so the per-target block is always a mapping once settings have parsed.
    agent_options = settings.eval.target_options.get("agent", {})
    profile = agent_options.get("llm_profile")
    if isinstance(profile, str) and profile in settings.llm_profiles:
        selected = settings.llm_profiles[profile]
        return selected.provider, selected.model, profile
    agent_name = agent_flag or agent_options.get("agent") or settings.eval.agent
    agent_settings = settings.agents.get(str(agent_name))
    override = agent_settings.model_override if agent_settings is not None else None
    if isinstance(override, str) and override.strip():
        return settings.llm.provider, override.strip(), None
    return base


def _provenance_labels(
    settings: Settings,
    target_name: str,
    scorer_names: list[str],
    *,
    agent_flag: str | None = None,
) -> dict[str, object]:
    """Caller-side provenance the runner cannot see (spec-0045 B6).

    Provider and model follow the client the ``agent`` target will dispatch
    through (profile, then per-agent model override, then the shared client);
    the profile is the one it stamps on every row, if any. Option mappings are
    digested rather than copied so a secret-shaped option never lands in a
    report.
    """
    provider, model, profile = _resolve_llm_labels(settings, target_name, agent_flag)
    return {
        "llm_provider": provider,
        "llm_model": model,
        "llm_profile": profile,
        "scorers": list(scorer_names),
        "scorer_options_sha256": digest_of(settings.eval.scorer_options),
        "target_options_sha256": digest_of(settings.eval.target_options.get(target_name, {})),
    }


def _build_scorer(names: list[str], scorer_options: dict[str, object]) -> Scorer:
    """One name → that scorer with the flat options; several → a ``composite``.

    With one scorer ``MANGOMAS_EVAL__SCORER_OPTIONS`` is handed to it whole
    (today's contract). With several (`-s a -s b`, spec-0045 B5) each child
    takes the sub-mapping under its own name, so one env var still configures
    every child, and the row records each child's result separately.
    """
    if len(names) == 1:
        return scorer_registry.get(names[0])(dict(scorer_options))
    children: list[dict[str, object]] = []
    for name in names:
        options = scorer_options.get(name)
        children.append(
            {"name": name, "options": dict(options) if isinstance(options, dict) else {}}
        )
    return scorer_registry.get(_COMPOSITE_SCORER)({"scorers": children})


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
    min_pass_pow_k: float | None = None,
    cfg: EvalSettings,
) -> tuple[bool, float | None, float | None, bool, float | None, float | None]:
    """Resolve effective gate config (CLI over settings) and validate thresholds.

    Returns ``(gating_engaged, min_mean, min_pass, fail_on_error,
    max_mean_cost_usd, min_pass_pow_k)``.
    An explicit ``--no-gate`` disables gating even when thresholds /
    ``fail_on_error`` / ``max_mean_cost_usd`` / ``min_pass_pow_k`` are
    configured via settings/env.
    Raises ``typer.Exit(EXIT_CONFIG_ERROR)`` on an out-of-range unit threshold
    or a non-finite / negative cost threshold — but only when gating is engaged,
    since an unused threshold should not block a run.
    """
    eff_min_mean = min_mean_score if min_mean_score is not None else cfg.min_mean_score
    eff_min_pass = min_pass_rate if min_pass_rate is not None else cfg.min_pass_rate
    eff_fail_on_error = fail_on_error if fail_on_error is not None else cfg.fail_on_error
    eff_max_mean_cost = (
        max_mean_cost_usd if max_mean_cost_usd is not None else cfg.max_mean_cost_usd
    )
    eff_min_pass_pow_k = min_pass_pow_k if min_pass_pow_k is not None else cfg.min_pass_pow_k
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
            or eff_min_pass_pow_k is not None
        )

    if gating_engaged:
        for label, val in (
            ("min-mean-score", eff_min_mean),
            ("min-pass-rate", eff_min_pass),
            ("min-pass-pow-k", eff_min_pass_pow_k),
        ):
            if val is not None and not 0.0 <= val <= 1.0:
                typer.echo(
                    f"Eval configuration error: {label} must be in [0.0, 1.0]; got {val}",
                    err=True,
                )
                raise typer.Exit(code=EXIT_CONFIG_ERROR)
        if eff_max_mean_cost is not None:
            try:
                require_non_negative_float(
                    eff_max_mean_cost,
                    owner="eval",
                    field="max-mean-cost-usd",
                )
            except ConfigError as exc:
                typer.echo(f"Eval configuration error: {exc}", err=True)
                raise typer.Exit(code=EXIT_CONFIG_ERROR) from exc
    return (
        gating_engaged,
        eff_min_mean,
        eff_min_pass,
        eff_fail_on_error,
        eff_max_mean_cost,
        eff_min_pass_pow_k,
    )


def _resolve_regression(
    *,
    baseline: str | None,
    max_mean_score_drop: float | None,
    max_pass_rate_drop: float | None,
    allow_new_failures: bool | None,
    cfg: EvalSettings,
) -> tuple[str | None, float | None, float | None, bool]:
    """Resolve effective regression-gate config (CLI flag over settings).

    The ``EvalSettings`` validator only ever sees env / settings values, so a
    CLI ``--max-*-drop`` used to reach the gate unchecked: ``nan`` compares
    false against every drop (gate never fails) and a negative value fails
    every run. Both are rejected here with exit 2, but only when a baseline is
    in play — an unused tolerance must not block a run (same rule as
    :func:`_resolve_gating`).
    """
    eff_baseline = baseline or cfg.baseline_path
    eff_mean_drop = (
        max_mean_score_drop if max_mean_score_drop is not None else cfg.max_mean_score_drop
    )
    eff_pass_drop = max_pass_rate_drop if max_pass_rate_drop is not None else cfg.max_pass_rate_drop
    if eff_baseline:
        checks = (("max-mean-score-drop", eff_mean_drop), ("max-pass-rate-drop", eff_pass_drop))
        for field, val in checks:
            if val is None:
                continue
            try:
                require_unit_float(val, owner="eval", field=field)
            except ConfigError as exc:
                typer.echo(f"Eval configuration error: {exc}", err=True)
                raise typer.Exit(code=EXIT_CONFIG_ERROR) from exc
    return (
        eff_baseline,
        eff_mean_drop,
        eff_pass_drop,
        allow_new_failures if allow_new_failures is not None else cfg.allow_new_failures,
    )
