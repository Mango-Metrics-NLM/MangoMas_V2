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
from typing import TYPE_CHECKING, Literal

from opentelemetry import trace

from mangomas.eval.runner import EXPECTED_ERROR_METADATA_KEY
from mangomas.utils.json_path import finite_number

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.eval.baseline import ReportDiff
    from mangomas.eval.judge_calibration import JudgeStatus
    from mangomas.eval.runner import EvalReport

logger = logging.getLogger(__name__)

_COST_UNAVAILABLE_NOTE = "mean_cost_usd unavailable — cost threshold not applied"
_COST_NON_FINITE_THRESHOLD_NOTE = "max_mean_cost_usd is not a finite number"
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
    the runner already returns a zeroed report for that case. ``max_mean_cost_usd``
    is USD (not a unit score); when the report has no ``mean_cost_usd`` the
    cost check is skipped rather than failed, so a quality-only run stays
    valid if an operator also set a cost threshold. ``min_pass_pow_k`` is the
    opposite: it fails *closed* when the run has fewer than
    :data:`MIN_TRIALS_FOR_PASS_POW_K` trials or no ``pass_pow_k`` at all,
    because a reliability threshold that a single attempt can satisfy is
    vacuous (spec-0046). When the report's judge is uncalibrated (spec-0047)
    the three score-derived thresholds are replaced by one informational
    note — the judge is *reported, not gated* — while ``fail_on_error`` and
    the cost threshold still apply.
    """
    pass_rate = report.passed / report.dataset_size if report.dataset_size else 0.0
    reasons: list[str] = []
    thresholds_configured = any(
        value is not None for value in (min_mean_score, min_pass_rate, min_pass_pow_k)
    )
    judge_excluded, judge_note = _judge_verdict(
        report.judge, thresholds_configured=thresholds_configured
    )

    if report.dataset_size == 0:
        # Nothing to gate — always passes; the threshold checks below are
        # skipped entirely rather than evaluated against a meaningless
        # pass_rate/mean_score of 0.0 (which would otherwise fail thresholds).
        reasons.append(_EMPTY_DATASET_NOTE)
    else:
        if judge_excluded and judge_note is not None:
            reasons.append(judge_note)
            # Refusal rows never reached the judge, so its demotion does not
            # cover them: the score thresholds still apply to that subset.
            reasons.extend(
                _scorer_independent_reasons(
                    report,
                    min_mean_score=min_mean_score,
                    min_pass_rate=min_pass_rate,
                    min_pass_pow_k=min_pass_pow_k,
                )
            )
        else:
            reasons.extend(
                _score_reasons(
                    report,
                    pass_rate,
                    min_mean_score=min_mean_score,
                    min_pass_rate=min_pass_rate,
                    min_pass_pow_k=min_pass_pow_k,
                )
            )
        if fail_on_error and report.errored > 0:
            reasons.append(f"{report.errored} row(s) errored (fail_on_error)")
        if max_mean_cost_usd is not None:
            reasons.extend(_cost_reasons(report, max_mean_cost_usd))

    informational = {_EMPTY_DATASET_NOTE, _COST_UNAVAILABLE_NOTE}
    if judge_excluded and judge_note is not None:
        informational.add(judge_note)
    failing = [reason for reason in reasons if reason not in informational]
    result = GateResult(
        passed=report.dataset_size == 0 or not failing,
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


def _normalize_threshold(raw: float, *, name: str) -> float | None:
    """Validate and coerce a numeric gate threshold, logging invalid inputs."""
    normalized = finite_number(raw)
    if normalized is None:
        logger.warning(
            "Eval gate threshold is non-finite or invalid; failing closed",
            extra={"event": "eval_gate_invalid_threshold", "threshold": name},
        )
    return normalized


def _score_reasons(
    report: EvalReport,
    pass_rate: float,
    *,
    min_mean_score: float | None,
    min_pass_rate: float | None,
    min_pass_pow_k: float | None,
) -> list[str]:
    """The three score-derived thresholds (mean, pass rate, pass^k)."""
    reasons: list[str] = []
    if min_mean_score is not None:
        # ``nan < x`` is always False, and ``bool`` subclasses ``int``, so
        # normalize both threshold and metric via ``finite_number``.
        limit = _normalize_threshold(min_mean_score, name="min_mean_score")
        actual_mean = finite_number(report.mean_score)
        if limit is None:
            reasons.append(_MEAN_SCORE_THRESHOLD_NON_FINITE_NOTE)
        elif actual_mean is None:
            reasons.append(_MEAN_SCORE_NON_FINITE_NOTE)
        elif actual_mean < limit:
            reasons.append(f"mean_score {actual_mean:.3f} < min_mean_score {limit:.3f}")
    if min_pass_rate is not None:
        rate_limit = _normalize_threshold(min_pass_rate, name="min_pass_rate")
        actual_rate = finite_number(pass_rate)
        if rate_limit is None:
            reasons.append(_RATE_THRESHOLD_NON_FINITE_NOTE)
        elif actual_rate is None:
            reasons.append(_RATE_NON_FINITE_NOTE)
        elif actual_rate < rate_limit:
            reasons.append(f"pass_rate {actual_rate:.3f} < min_pass_rate {rate_limit:.3f}")
    if min_pass_pow_k is not None:
        reasons.extend(_reliability_reasons(report, min_pass_pow_k))
    return reasons


def scorer_independent_row_ids(report: EvalReport) -> frozenset[str]:
    """Ids of rows judged without the scorer — ``expected_error`` refusal checks."""
    return frozenset(r.row_id for r in report.rows if EXPECTED_ERROR_METADATA_KEY in r.metadata)


def _scorer_independent_reasons(
    report: EvalReport,
    *,
    min_mean_score: float | None,
    min_pass_rate: float | None,
    min_pass_pow_k: float | None,
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
    found = _score_reasons(
        subset,
        passed / len(rows),
        min_mean_score=min_mean_score,
        min_pass_rate=min_pass_rate,
        min_pass_pow_k=min_pass_pow_k,
    )
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


def _cost_reasons(report: EvalReport, max_mean_cost_usd: float) -> list[str]:
    """The cost ladder: an unavailable mean is an informational skip, never a fail."""
    limit = _normalize_threshold(max_mean_cost_usd, name="max_mean_cost_usd")
    if limit is None:
        return [_COST_NON_FINITE_THRESHOLD_NOTE]
    if report.mean_cost_usd is None:
        return [_COST_UNAVAILABLE_NOTE]
    actual = finite_number(report.mean_cost_usd)
    if actual is None:
        return [_COST_NON_FINITE_MEAN_NOTE]
    if actual > limit:
        return [f"mean_cost_usd {actual} > max_mean_cost_usd {limit}"]
    return []


def _reliability_reasons(report: EvalReport, min_pass_pow_k: float) -> list[str]:
    """The ``min_pass_pow_k`` verdict: fails closed when pass^k is not measured.

    A single attempt cannot measure reliability, so a run with fewer than
    :data:`MIN_TRIALS_FOR_PASS_POW_K` trials fails the threshold outright
    rather than skipping it (the cost gate's skip-when-unavailable rule is
    deliberately *not* reused here — spec-0046).
    """
    limit = _normalize_threshold(min_pass_pow_k, name="min_pass_pow_k")
    if limit is None:
        return [_POW_K_THRESHOLD_NON_FINITE_NOTE]
    if report.trials < MIN_TRIALS_FOR_PASS_POW_K:
        return [
            f"min_pass_pow_k requires trials >= {MIN_TRIALS_FOR_PASS_POW_K}; "
            f"the run used trials={report.trials}"
        ]
    actual = None if report.pass_pow_k is None else finite_number(report.pass_pow_k)
    if actual is None:
        return [_RELIABILITY_UNAVAILABLE_NOTE]
    if actual < limit:
        return [f"pass_pow_k {actual:.3f} < min_pass_pow_k {limit:.3f}"]
    return []


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
    regression criterion is configured, such a ``judge`` turns the verdict into
    one informational note and ``judge_excluded=True`` — whether or not the
    raw deltas would have failed, so the flag never depends on luck (spec-0047).
    Rows in *scorer_independent_rows* (refusal checks) are exempt from that
    demotion: with ``allow_new_failures=False`` a regressed refusal row still fails.
    """
    reasons: list[str] = []
    mean_drop = -diff.mean_score_delta
    pass_drop = -diff.pass_rate_delta
    if max_mean_score_drop is not None:
        mean_drop_limit = _normalize_threshold(max_mean_score_drop, name="max_mean_score_drop")
        actual_mean_drop = finite_number(mean_drop)
        if mean_drop_limit is None:
            reasons.append(_MEAN_DROP_THRESHOLD_NON_FINITE_NOTE)
        elif actual_mean_drop is None:
            # A NaN on either side of the diff makes ``>`` always False.
            reasons.append(_MEAN_DROP_NON_FINITE_NOTE)
        elif actual_mean_drop > mean_drop_limit:
            reasons.append(f"mean_score dropped {actual_mean_drop:.3f} > max {mean_drop_limit:.3f}")
    if max_pass_rate_drop is not None:
        rate_drop_limit = _normalize_threshold(max_pass_rate_drop, name="max_pass_rate_drop")
        actual_rate_drop = finite_number(pass_drop)
        if rate_drop_limit is None:
            reasons.append(_RATE_DROP_THRESHOLD_NON_FINITE_NOTE)
        elif actual_rate_drop is None:
            reasons.append(_RATE_DROP_NON_FINITE_NOTE)
        elif actual_rate_drop > rate_drop_limit:
            reasons.append(f"pass_rate dropped {actual_rate_drop:.3f} > max {rate_drop_limit:.3f}")
    if not allow_new_failures and diff.regressed_rows:
        shown = ", ".join(diff.regressed_rows[:5])
        reasons.append(f"{len(diff.regressed_rows)} row regression(s): {shown}")
    criteria_configured = (
        max_mean_score_drop is not None or max_pass_rate_drop is not None or not allow_new_failures
    )
    judge_excluded = judge is not None and not judge.calibrated and criteria_configured
    judge_note = judge.summary() if judge is not None else None
    failing = reasons
    if judge_excluded and judge_note is not None:
        judge_note = f"{judge_note} — {_JUDGE_REGRESSION_NOT_APPLIED}"
        independent = [r for r in diff.regressed_rows if r in set(scorer_independent_rows)]
        failing = (
            [f"{_SCORER_INDEPENDENT}: {len(independent)} regression(s): {', '.join(independent)}"]
            if not allow_new_failures and independent
            else []
        )
        reasons = [judge_note, *failing]
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
