"""Quality gate — turns an :class:`EvalReport` into a pass/fail CI verdict.

The gate is a deterministic function over the aggregate report — its only
side effect is emitting a telemetry span plus one structured log line (it never
mutates the report or touches external state). It is
*off by default*: when no thresholds are configured and ``fail_on_error`` is
``False`` it returns ``passed=True`` so existing ``mangomas eval`` runs keep
exit code 0. When engaged, the CLI maps a failing gate to exit code 3 (distinct
from 1=runtime and 2=config).

Metric semantics (must stay aligned with :class:`~mangomas.eval.runner.EvalRunner`):

* ``mean_score`` is averaged over **non-errored** rows only (errored rows are
  excluded from the numerator and denominator by the runner).
* ``pass_rate`` here is ``report.passed / report.dataset_size`` — errored rows
  **are** in the denominator, so they drag ``pass_rate`` down but not
  ``mean_score``. Set ``fail_on_error=True`` to make any errored row fail the
  gate outright, regardless of thresholds.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field, replace
from fractions import Fraction
from typing import TYPE_CHECKING, Literal

from opentelemetry import trace

from mangomas.eval.runner import EXPECTED_ERROR_METADATA_KEY
from mangomas.utils.json_path import finite_real, to_exact_fraction

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.eval.baseline import ReportDiff
    from mangomas.eval.judge_calibration import JudgeStatus
    from mangomas.eval.runner import EvalReport

logger = logging.getLogger(__name__)

_COST_UNAVAILABLE_NOTE = "mean_cost_usd unavailable — cost threshold not applied"
_COST_NON_FINITE_THRESHOLD_NOTE = "max_mean_cost_usd is not a finite number"
_COST_NEGATIVE_THRESHOLD_NOTE = "max_mean_cost_usd must be >= 0"
_COST_NON_FINITE_MEAN_NOTE = "mean_cost_usd is not a finite number"
_EMPTY_DATASET_NOTE = "empty dataset — nothing to gate"
_MEAN_SCORE_NON_FINITE_NOTE = "mean_score is not a finite number"
_MEAN_SCORE_THRESHOLD_NON_FINITE_NOTE = "min_mean_score threshold is non-finite or invalid"
_RATE_NON_FINITE_NOTE = "pass_rate is non-finite"
_RATE_THRESHOLD_NON_FINITE_NOTE = "min_pass_rate threshold is non-finite or invalid"
_POW_K_THRESHOLD_NON_FINITE_NOTE = "min_pass_pow_k threshold is non-finite or invalid"
_MEAN_DROP_NON_FINITE_NOTE = "mean_score drop is not a finite number"
_MEAN_DROP_THRESHOLD_NON_FINITE_NOTE = "max_mean_score_drop threshold is non-finite or invalid"
_RATE_DROP_NON_FINITE_NOTE = "pass_rate drop is not a finite number"
_RATE_DROP_THRESHOLD_NON_FINITE_NOTE = "max_pass_rate_drop threshold is non-finite or invalid"
_RELIABILITY_UNAVAILABLE_NOTE = "pass_pow_k unavailable — min_pass_pow_k not met"
#: ``min_pass_pow_k`` needs at least this many trials to mean anything.
MIN_TRIALS_FOR_PASS_POW_K = 2
_JUDGE_THRESHOLDS_NOT_APPLIED = "score thresholds not applied"
_JUDGE_REGRESSION_NOT_APPLIED = "regression thresholds not applied"
_SCORER_INDEPENDENT = "scorer-independent rows"


@dataclass(frozen=True)
class _ScoreLimits:
    min_mean_score: Fraction | None = None
    min_pass_rate: Fraction | None = None
    min_pass_pow_k: Fraction | None = None


@dataclass(frozen=True)
class GateResult:
    """Outcome of evaluating a quality gate against an :class:`EvalReport`.

    ``reasons`` lists each failed criterion in human-readable form. It is empty
    on a clean pass, but a *passing* result may still carry a single
    informational note (e.g. an empty dataset has nothing to gate).
    ``actual_mean_score`` / ``actual_pass_rate``
    are surfaced (alongside the configured thresholds) so a sink can emit the
    full gate context without recomputing it.

    ``kind`` discriminates a threshold-gate verdict (:func:`evaluate_gate`,
    which populates ``min_mean_score``/``min_pass_rate``/``fail_on_error``/
    ``errored`` plus the additive cost fields) from a regression-gate verdict
    (:func:`evaluate_regression_gate`, which leaves those at their defaults).
    :func:`merge_gate_results` uses it to source the threshold fields from the
    correct verdict regardless of the order its inputs are passed in.
    """

    passed: bool
    actual_mean_score: float
    actual_pass_rate: float
    min_mean_score: float | None = None
    min_pass_rate: float | None = None
    fail_on_error: bool = False
    errored: int = 0
    reasons: list[str] = field(default_factory=list)
    kind: Literal["threshold", "regression"] = "threshold"
    actual_mean_cost_usd: float | None = None
    max_mean_cost_usd: float | None = None
    # spec-0046: the reliability threshold and the report's pass^k.
    min_pass_pow_k: float | None = None
    actual_pass_pow_k: float | None = None
    # spec-0047: set whenever the report carries a judge status; ``judge_excluded``
    # is True when score-derived thresholds were demoted to informational
    # because the judge is uncalibrated — machine-readable, so a consumer can
    # refuse a "green" that rests on an uncalibrated judge.
    judge_excluded: bool = False
    judge_note: str | None = None


# Sentinel supplying GateResult's own threshold-field defaults when
# merge_gate_results() has no kind="threshold" verdict among its inputs.
# Never surfaced directly — only its threshold fields are read.
_DEFAULT_THRESHOLDS = GateResult(passed=True, actual_mean_score=0.0, actual_pass_rate=0.0)


def _validate_score_thresholds(
    *,
    min_mean_score: float | None,
    min_pass_rate: float | None,
    min_pass_pow_k: float | None,
) -> tuple[_ScoreLimits, list[str]]:
    """Validate configured score-derived thresholds up front so bad limits always fail closed."""
    config_failures: list[str] = []
    limits: dict[str, Fraction | None] = {}
    specs = (
        ("min_mean_score", min_mean_score, _MEAN_SCORE_THRESHOLD_NON_FINITE_NOTE),
        ("min_pass_rate", min_pass_rate, _RATE_THRESHOLD_NON_FINITE_NOTE),
        ("min_pass_pow_k", min_pass_pow_k, _POW_K_THRESHOLD_NON_FINITE_NOTE),
    )
    for field_name, raw, failure_note in specs:
        if raw is None:
            limits[field_name] = None
        else:
            limit = _normalize_threshold(raw, name=field_name)
            limits[field_name] = limit
            if limit is None:
                config_failures.append(failure_note)
    return (
        _ScoreLimits(
            min_mean_score=limits["min_mean_score"],
            min_pass_rate=limits["min_pass_rate"],
            min_pass_pow_k=limits["min_pass_pow_k"],
        ),
        config_failures,
    )


def _validate_cost_threshold(raw: float) -> tuple[Fraction | None, str | None]:
    """Validate ``max_mean_cost_usd`` (non-negative finite real), logging invalid inputs."""
    limit = _normalize_threshold(raw, name="max_mean_cost_usd", min_value=0.0, max_value=None)
    if limit is None:
        note = (
            _COST_NON_FINITE_THRESHOLD_NOTE
            if finite_real(raw) is None
            else _COST_NEGATIVE_THRESHOLD_NOTE
        )
        return None, note
    return limit, None


def evaluate_gate(
    report: EvalReport,
    *,
    min_mean_score: float | None = None,
    min_pass_rate: float | None = None,
    fail_on_error: bool = False,
    max_mean_cost_usd: float | None = None,
    min_pass_pow_k: float | None = None,
) -> GateResult:
    """Return a :class:`GateResult` for *report* against the given thresholds.

    A no-op gate (all thresholds ``None`` and ``fail_on_error=False``) always
    passes. An empty dataset passes with a note — there is nothing to gate, and
    the runner already returns a zeroed report for that case — unless a
    configured threshold itself is non-finite or out of range. ``max_mean_cost_usd``
    is USD (not a unit score); when the report has no ``mean_cost_usd`` the
    cost check is skipped rather than failed, so a quality-only run stays
    valid if an operator also set a cost threshold. ``min_pass_pow_k`` is the
    opposite: it fails *closed* when the run has fewer than
    :data:`MIN_TRIALS_FOR_PASS_POW_K` trials or no ``pass_pow_k`` at all,
    because a reliability threshold that a single attempt can satisfy is
    vacuous (spec-0046). When the report's judge is uncalibrated (spec-0047)
    valid score-derived thresholds are replaced by one informational
    note — the judge is *reported, not gated* — while invalid threshold
    configurations, ``fail_on_error``, and the cost threshold still apply.
    """
    pass_rate = report.passed / report.dataset_size if report.dataset_size else 0.0
    reasons: list[str] = []
    score_limits, score_config_failures = _validate_score_thresholds(
        min_mean_score=min_mean_score,
        min_pass_rate=min_pass_rate,
        min_pass_pow_k=min_pass_pow_k,
    )
    cost_limit: Fraction | None = None
    cost_config_failures: list[str] = []
    if max_mean_cost_usd is not None:
        cost_limit, cost_failure = _validate_cost_threshold(max_mean_cost_usd)
        if cost_failure is not None:
            cost_config_failures.append(cost_failure)

    thresholds_configured = any(
        value is not None for value in (min_mean_score, min_pass_rate, min_pass_pow_k)
    )
    judge_excluded, judge_note = _judge_verdict(
        report.judge, thresholds_configured=thresholds_configured
    )

    if report.dataset_size == 0:
        # Nothing to gate — valid thresholds pass with an informational note;
        # invalid/non-finite threshold configs still fail closed.
        reasons.append(_EMPTY_DATASET_NOTE)
        reasons.extend(score_config_failures)
        reasons.extend(cost_config_failures)
    else:
        if judge_excluded and judge_note is not None:
            reasons.append(judge_note)
            reasons.extend(score_config_failures)
            # Refusal rows never reached the judge, so its demotion does not
            # cover them: valid score thresholds still apply to that subset.
            reasons.extend(_scorer_independent_reasons(report, limits=score_limits))
        else:
            reasons.extend(score_config_failures)
            reasons.extend(_evaluate_score_limits(report, pass_rate, score_limits))
        if fail_on_error and report.errored > 0:
            reasons.append(f"{report.errored} row(s) errored (fail_on_error)")
        if cost_config_failures:
            reasons.extend(cost_config_failures)
        elif cost_limit is not None:
            reasons.extend(_evaluate_cost_limit(report, cost_limit))

    informational = {_EMPTY_DATASET_NOTE, _COST_UNAVAILABLE_NOTE}
    if judge_excluded and judge_note is not None:
        informational.add(judge_note)
    failing = [reason for reason in reasons if reason not in informational]
    result = GateResult(
        passed=not failing,
        actual_mean_score=report.mean_score,
        actual_pass_rate=pass_rate,
        min_mean_score=min_mean_score,
        min_pass_rate=min_pass_rate,
        fail_on_error=fail_on_error,
        errored=report.errored,
        reasons=reasons,
        actual_mean_cost_usd=report.mean_cost_usd,
        max_mean_cost_usd=max_mean_cost_usd,
        min_pass_pow_k=min_pass_pow_k,
        actual_pass_pow_k=report.pass_pow_k,
        judge_excluded=judge_excluded,
        judge_note=judge_note,
    )
    _log(result)
    return result


def _normalize_threshold(
    raw: float,
    *,
    name: str,
    min_value: float = 0.0,
    max_value: float | None = 1.0,
) -> Fraction | None:
    """Validate a numeric gate threshold and return its exact :class:`Fraction`."""
    real = finite_real(raw)
    if real is None:
        logger.warning(
            "Eval gate threshold is non-finite or invalid; failing closed",
            extra={"event": "eval_gate_invalid_threshold", "threshold": name},
        )
        return None
    exact = to_exact_fraction(real)
    lower = to_exact_fraction(min_value)
    upper = None if max_value is None else to_exact_fraction(max_value)
    if exact < lower or (upper is not None and exact > upper):
        logger.warning(
            "Eval gate threshold is out of range; failing closed",
            extra={
                "event": "eval_gate_invalid_threshold",
                "threshold": name,
                "min_value": min_value,
                "max_value": max_value,
            },
        )
        return None
    return exact


def _evaluate_score_limits(
    report: EvalReport,
    pass_rate: float,
    limits: _ScoreLimits,
) -> list[str]:
    """Compare report metrics against pre-validated exact score limits."""
    reasons: list[str] = []
    if limits.min_mean_score is not None:
        actual_mean = finite_real(report.mean_score)
        if actual_mean is None:
            reasons.append(_MEAN_SCORE_NON_FINITE_NOTE)
        elif to_exact_fraction(actual_mean) < limits.min_mean_score:
            reasons.append(
                f"mean_score {float(actual_mean):.3f} < min_mean_score "
                f"{float(limits.min_mean_score):.3f}"
            )
    if limits.min_pass_rate is not None:
        actual_rate = finite_real(pass_rate)
        if actual_rate is None:
            reasons.append(_RATE_NON_FINITE_NOTE)
        elif to_exact_fraction(actual_rate) < limits.min_pass_rate:
            reasons.append(
                f"pass_rate {float(actual_rate):.3f} < min_pass_rate "
                f"{float(limits.min_pass_rate):.3f}"
            )
    if limits.min_pass_pow_k is not None:
        reasons.extend(_evaluate_reliability_limit(report, limits.min_pass_pow_k))
    return reasons


def _score_reasons(
    report: EvalReport,
    pass_rate: float,
    *,
    min_mean_score: float | None,
    min_pass_rate: float | None,
    min_pass_pow_k: float | None,
) -> list[str]:
    """The three score-derived thresholds (mean, pass rate, pass^k)."""
    limits, failures = _validate_score_thresholds(
        min_mean_score=min_mean_score,
        min_pass_rate=min_pass_rate,
        min_pass_pow_k=min_pass_pow_k,
    )
    return [*failures, *_evaluate_score_limits(report, pass_rate, limits)]


def scorer_independent_row_ids(report: EvalReport) -> frozenset[str]:
    """Ids of rows judged without the scorer — ``expected_error`` refusal checks."""
    return frozenset(r.row_id for r in report.rows if EXPECTED_ERROR_METADATA_KEY in r.metadata)


def _scorer_independent_reasons(
    report: EvalReport,
    *,
    limits: _ScoreLimits,
) -> list[str]:
    """Score thresholds over the rows an uncalibrated judge never graded.

    An ``expected_error`` row passes on the named refusal and fails on an
    answer without consulting the scorer, so its verdict is deterministic and
    an uncalibrated judge elsewhere in the run is no reason to waive it
    (Copilot review on #126). The subset is gated like a report of its own.
    """
    rows = [r for r in report.rows if EXPECTED_ERROR_METADATA_KEY in r.metadata]
    if not rows:
        return []
    passed = sum(1 for r in rows if r.passed and r.error is None)
    scored = [r.score for r in rows if r.error is None]
    subset = replace(
        report,
        dataset_size=len(rows),
        passed=passed,
        mean_score=sum(scored) / len(scored) if scored else 0.0,
        pass_pow_k=passed / len(rows),
        rows=rows,
    )
    found = _evaluate_score_limits(subset, passed / len(rows), limits)
    return [f"{_SCORER_INDEPENDENT} ({len(rows)}): {reason}" for reason in found]


def _judge_verdict(
    judge: JudgeStatus | None, *, thresholds_configured: bool
) -> tuple[bool, str | None]:
    """``(excluded, note)`` for the report's judge (spec-0047).

    The note exists whenever a judge took part, calibrated or not, so the
    self-preference claim is never dropped from a verdict. Exclusion — the
    note replacing the score thresholds as an informational reason — happens
    only when the judge is uncalibrated *and* a score threshold was actually
    configured; otherwise "thresholds not applied" would be untrue.
    """
    if judge is None:
        return False, None
    note = judge.summary()
    if judge.calibrated or not thresholds_configured:
        return False, note
    return True, f"{note} — {_JUDGE_THRESHOLDS_NOT_APPLIED}"


def _evaluate_cost_limit(report: EvalReport, limit: Fraction) -> list[str]:
    """The cost ladder against a pre-validated exact non-negative USD limit."""
    if report.mean_cost_usd is None:
        return [_COST_UNAVAILABLE_NOTE]
    actual = finite_real(report.mean_cost_usd)
    if actual is None:
        return [_COST_NON_FINITE_MEAN_NOTE]
    if to_exact_fraction(actual) > limit:
        return [f"mean_cost_usd {actual} > max_mean_cost_usd {float(limit)}"]
    return []


def _evaluate_reliability_limit(report: EvalReport, limit: Fraction) -> list[str]:
    """The ``min_pass_pow_k`` verdict against a pre-validated exact unit limit.

    A single attempt cannot measure reliability, so a run with fewer than
    :data:`MIN_TRIALS_FOR_PASS_POW_K` trials fails the threshold outright
    rather than skipping it (the cost gate's skip-when-unavailable rule is
    deliberately *not* reused here — spec-0046).
    """
    if report.trials < MIN_TRIALS_FOR_PASS_POW_K:
        return [
            f"min_pass_pow_k requires trials >= {MIN_TRIALS_FOR_PASS_POW_K}; "
            f"the run used trials={report.trials}"
        ]
    actual = None if report.pass_pow_k is None else finite_real(report.pass_pow_k)
    if actual is None:
        return [_RELIABILITY_UNAVAILABLE_NOTE]
    if to_exact_fraction(actual) < limit:
        return [f"pass_pow_k {float(actual):.3f} < min_pass_pow_k {float(limit):.3f}"]
    return []


def _evaluate_drop_criterion(
    *,
    threshold_raw: float | None,
    delta_raw: float,
    threshold_name: str,
    metric_name: str,
    invalid_threshold_note: str,
    non_finite_delta_note: str,
) -> tuple[str | None, str | None]:
    """Evaluate one regression drop threshold, returning ``(config_failure, metric_failure)``."""
    if threshold_raw is None:
        return None, None
    limit = _normalize_threshold(threshold_raw, name=threshold_name)
    if limit is None:
        return invalid_threshold_note, None
    delta = finite_real(delta_raw)
    if delta is None:
        # Check finiteness on the original value before negating (-True == -1).
        return None, non_finite_delta_note
    actual_drop = -to_exact_fraction(delta)
    if actual_drop > limit:
        return None, f"{metric_name} dropped {float(actual_drop):.3f} > max {float(limit):.3f}"
    return None, None


def evaluate_regression_gate(
    diff: ReportDiff,
    *,
    max_mean_score_drop: float | None = None,
    max_pass_rate_drop: float | None = None,
    allow_new_failures: bool = True,
    judge: JudgeStatus | None = None,
    scorer_independent_rows: Collection[str] = (),
) -> GateResult:
    """Return a :class:`GateResult` for a baseline *diff* (regression gating).

    Reuses :class:`GateResult` (so existing sinks render it unchanged): the
    ``actual_*`` fields carry the *current* run's metrics. A "drop" is
    ``baseline - current`` (positive = regression). A no-op gate
    (``max_*_drop=None`` and ``allow_new_failures=True``) always passes.
    Score deltas from an uncalibrated *judge* are judge-derived too: when any
    regression criterion is configured, such a ``judge`` turns judge-derived
    score-drop failures into one informational note and ``judge_excluded=True``
    — whether or not the raw deltas would have failed, so the flag never
    depends on luck (spec-0047). Invalid threshold configurations and
    regressed *scorer_independent_rows* (refusal checks under
    ``allow_new_failures=False``) are never waived by judge demotion.
    """
    config_failures: list[str] = []
    metric_failures: list[str] = []
    for threshold_raw, delta_raw, threshold_name, metric_name, invalid_note, non_finite_note in (
        (
            max_mean_score_drop,
            diff.mean_score_delta,
            "max_mean_score_drop",
            "mean_score",
            _MEAN_DROP_THRESHOLD_NON_FINITE_NOTE,
            _MEAN_DROP_NON_FINITE_NOTE,
        ),
        (
            max_pass_rate_drop,
            diff.pass_rate_delta,
            "max_pass_rate_drop",
            "pass_rate",
            _RATE_DROP_THRESHOLD_NON_FINITE_NOTE,
            _RATE_DROP_NON_FINITE_NOTE,
        ),
    ):
        cfg_err, metric_err = _evaluate_drop_criterion(
            threshold_raw=threshold_raw,
            delta_raw=delta_raw,
            threshold_name=threshold_name,
            metric_name=metric_name,
            invalid_threshold_note=invalid_note,
            non_finite_delta_note=non_finite_note,
        )
        if cfg_err is not None:
            config_failures.append(cfg_err)
        if metric_err is not None:
            metric_failures.append(metric_err)
    if not allow_new_failures and diff.regressed_rows:
        shown = ", ".join(diff.regressed_rows[:5])
        metric_failures.append(f"{len(diff.regressed_rows)} row regression(s): {shown}")
    criteria_configured = (
        max_mean_score_drop is not None or max_pass_rate_drop is not None or not allow_new_failures
    )
    judge_excluded = judge is not None and not judge.calibrated and criteria_configured
    judge_note = judge.summary() if judge is not None else None
    if judge_excluded and judge_note is not None:
        judge_note = f"{judge_note} — {_JUDGE_REGRESSION_NOT_APPLIED}"
        independent = [r for r in diff.regressed_rows if r in set(scorer_independent_rows)]
        independent_failures = (
            [f"{_SCORER_INDEPENDENT}: {len(independent)} regression(s): {', '.join(independent)}"]
            if not allow_new_failures and independent
            else []
        )
        failing = [*config_failures, *independent_failures]
        reasons = [judge_note, *failing]
    else:
        failing = [*config_failures, *metric_failures]
        reasons = failing
    result = GateResult(
        passed=not failing,
        actual_mean_score=diff.current_mean_score,
        actual_pass_rate=diff.current_pass_rate,
        reasons=reasons,
        kind="regression",
        judge_excluded=judge_excluded,
        judge_note=judge_note,
    )
    _log_regression(result, diff)
    return result


def merge_gate_results(results: Sequence[GateResult | None]) -> GateResult | None:
    """Combine multiple gate verdicts into one (logical AND of ``passed``).

    Returns ``None`` when no verdict is present, the sole verdict unchanged when
    only one is present (byte-compatible with single-gate runs), or a merged
    verdict carrying every reason when both a threshold and a regression gate
    are engaged.

    The merged verdict's threshold fields (``min_mean_score``/``min_pass_rate``/
    ``fail_on_error``/``errored`` plus the additive cost fields) are sourced
    from the ``kind="threshold"`` verdict specifically — *not* positionally
    from ``results[0]`` — so the merge is correct regardless of the order the
    caller passes its gates in.
    When no threshold-kind verdict is present (e.g. only a regression gate),
    the defaults on :class:`GateResult` apply.
    """
    present = [r for r in results if r is not None]
    if not present:
        return None
    if len(present) == 1:
        return present[0]
    threshold_candidates = [r for r in present if r.kind == "threshold"]
    # No threshold-kind verdict among `present` (e.g. only regression gates
    # were engaged): fall back to GateResult's own field defaults, matching
    # the pre-``kind`` behaviour for a regression-only merge.
    threshold_result = threshold_candidates[0] if threshold_candidates else _DEFAULT_THRESHOLDS
    reasons: list[str] = [reason for r in present for reason in r.reasons]
    ordered = [threshold_result, *(r for r in present if r is not threshold_result)]
    excluded_note = next(
        (r.judge_note for r in ordered if r.judge_excluded and r.judge_note is not None),
        None,
    )
    fallback_note = threshold_result.judge_note or next(
        (r.judge_note for r in present if r.judge_note is not None),
        None,
    )
    return GateResult(
        passed=all(r.passed for r in present),
        actual_mean_score=present[0].actual_mean_score,
        actual_pass_rate=present[0].actual_pass_rate,
        min_mean_score=threshold_result.min_mean_score,
        min_pass_rate=threshold_result.min_pass_rate,
        fail_on_error=threshold_result.fail_on_error,
        errored=threshold_result.errored,
        reasons=reasons,
        actual_mean_cost_usd=threshold_result.actual_mean_cost_usd,
        max_mean_cost_usd=threshold_result.max_mean_cost_usd,
        min_pass_pow_k=threshold_result.min_pass_pow_k,
        actual_pass_pow_k=threshold_result.actual_pass_pow_k,
        judge_excluded=any(r.judge_excluded for r in present),
        judge_note=excluded_note or fallback_note,
    )


def _log_regression(result: GateResult, diff: ReportDiff) -> None:
    with trace.get_tracer(__name__).start_as_current_span("eval.regression_gate") as span:
        span.set_attribute("gate.passed", result.passed)
        span.set_attribute("gate.mean_score_delta", diff.mean_score_delta)
        span.set_attribute("gate.pass_rate_delta", diff.pass_rate_delta)
        span.set_attribute("gate.regressed_count", len(diff.regressed_rows))
    logger.info(
        "Eval regression gate evaluated",
        extra={
            "event": "eval_regression_gate",
            "passed": result.passed,
            "mean_score_delta": diff.mean_score_delta,
            "pass_rate_delta": diff.pass_rate_delta,
            "regressed": len(diff.regressed_rows),
            "reasons": result.reasons,
        },
    )


def _log(result: GateResult) -> None:
    with trace.get_tracer(__name__).start_as_current_span("eval.gate") as span:
        span.set_attribute("gate.passed", result.passed)
        span.set_attribute("gate.mean_score", result.actual_mean_score)
        span.set_attribute("gate.pass_rate", result.actual_pass_rate)
        if result.min_mean_score is not None:
            span.set_attribute("gate.min_mean_score", result.min_mean_score)
        if result.min_pass_rate is not None:
            span.set_attribute("gate.min_pass_rate", result.min_pass_rate)
        if result.actual_mean_cost_usd is not None:
            span.set_attribute("gate.mean_cost_usd", result.actual_mean_cost_usd)
        if result.max_mean_cost_usd is not None:
            span.set_attribute("gate.max_mean_cost_usd", result.max_mean_cost_usd)
        if result.actual_pass_pow_k is not None:
            span.set_attribute("gate.pass_pow_k", result.actual_pass_pow_k)
        if result.min_pass_pow_k is not None:
            span.set_attribute("gate.min_pass_pow_k", result.min_pass_pow_k)
        span.set_attribute("gate.judge_excluded", result.judge_excluded)
    logger.info(
        "Eval gate evaluated",
        extra={
            "event": "eval_gate",
            "passed": result.passed,
            "mean_score": result.actual_mean_score,
            "pass_rate": result.actual_pass_rate,
            "mean_cost_usd": result.actual_mean_cost_usd,
            "pass_pow_k": result.actual_pass_pow_k,
            "judge_excluded": result.judge_excluded,
            "judge_note": result.judge_note,
            "errored": result.errored,
            "reasons": result.reasons,
        },
    )
