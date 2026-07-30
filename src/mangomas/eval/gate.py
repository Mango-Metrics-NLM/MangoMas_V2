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
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from mangomas.telemetry import get_tracer

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.eval.baseline import ReportDiff
    from mangomas.eval.runner import EvalReport

logger = logging.getLogger(__name__)
_tracer = get_tracer(__name__)


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
    ``errored``) from a regression-gate verdict (:func:`evaluate_regression_gate`,
    which leaves those four at their defaults). :func:`merge_gate_results`
    uses it to source the threshold fields from the correct verdict
    regardless of the order its inputs are passed in.
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


# Sentinel supplying GateResult's own threshold-field defaults when
# merge_gate_results() has no kind="threshold" verdict among its inputs.
# Never surfaced directly — only its four threshold fields are read.
_DEFAULT_THRESHOLDS = GateResult(passed=True, actual_mean_score=0.0, actual_pass_rate=0.0)


def evaluate_gate(
    report: EvalReport,
    *,
    min_mean_score: float | None = None,
    min_pass_rate: float | None = None,
    fail_on_error: bool = False,
) -> GateResult:
    """Return a :class:`GateResult` for *report* against the given thresholds.

    A no-op gate (all thresholds ``None`` and ``fail_on_error=False``) always
    passes. An empty dataset passes with a note — there is nothing to gate, and
    the runner already returns a zeroed report for that case.
    """
    pass_rate = report.passed / report.dataset_size if report.dataset_size else 0.0
    reasons: list[str] = []

    if report.dataset_size == 0:
        # Nothing to gate — always passes; the threshold checks below are
        # skipped entirely rather than evaluated against a meaningless
        # pass_rate/mean_score of 0.0 (which would otherwise fail thresholds).
        reasons.append("empty dataset — nothing to gate")
    else:
        if min_mean_score is not None and report.mean_score < min_mean_score:
            reasons.append(
                f"mean_score {report.mean_score:.3f} < min_mean_score {min_mean_score:.3f}"
            )
        if min_pass_rate is not None and pass_rate < min_pass_rate:
            reasons.append(f"pass_rate {pass_rate:.3f} < min_pass_rate {min_pass_rate:.3f}")
        if fail_on_error and report.errored > 0:
            reasons.append(f"{report.errored} row(s) errored (fail_on_error)")

    result = GateResult(
        passed=report.dataset_size == 0 or not reasons,
        actual_mean_score=report.mean_score,
        actual_pass_rate=pass_rate,
        min_mean_score=min_mean_score,
        min_pass_rate=min_pass_rate,
        fail_on_error=fail_on_error,
        errored=report.errored,
        reasons=reasons,
    )
    _log(result)
    return result


def evaluate_regression_gate(
    diff: ReportDiff,
    *,
    max_mean_score_drop: float | None = None,
    max_pass_rate_drop: float | None = None,
    allow_new_failures: bool = True,
) -> GateResult:
    """Return a :class:`GateResult` for a baseline *diff* (regression gating).

    Reuses :class:`GateResult` (so existing sinks render it unchanged): the
    ``actual_*`` fields carry the *current* run's metrics. A "drop" is
    ``baseline - current`` (positive = regression). A no-op gate
    (``max_*_drop=None`` and ``allow_new_failures=True``) always passes.
    """
    reasons: list[str] = []
    mean_drop = -diff.mean_score_delta
    pass_drop = -diff.pass_rate_delta
    if max_mean_score_drop is not None and mean_drop > max_mean_score_drop:
        reasons.append(f"mean_score dropped {mean_drop:.3f} > max {max_mean_score_drop:.3f}")
    if max_pass_rate_drop is not None and pass_drop > max_pass_rate_drop:
        reasons.append(f"pass_rate dropped {pass_drop:.3f} > max {max_pass_rate_drop:.3f}")
    if not allow_new_failures and diff.regressed_rows:
        shown = ", ".join(diff.regressed_rows[:5])
        reasons.append(f"{len(diff.regressed_rows)} row regression(s): {shown}")
    result = GateResult(
        passed=not reasons,
        actual_mean_score=diff.current_mean_score,
        actual_pass_rate=diff.current_pass_rate,
        reasons=reasons,
        kind="regression",
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
    ``fail_on_error``/``errored``) are sourced from the ``kind="threshold"``
    verdict specifically — *not* positionally from ``results[0]`` — so the
    merge is correct regardless of the order the caller passes its gates in.
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
    return GateResult(
        passed=all(r.passed for r in present),
        actual_mean_score=present[0].actual_mean_score,
        actual_pass_rate=present[0].actual_pass_rate,
        min_mean_score=threshold_result.min_mean_score,
        min_pass_rate=threshold_result.min_pass_rate,
        fail_on_error=threshold_result.fail_on_error,
        errored=threshold_result.errored,
        reasons=reasons,
    )


def _log_regression(result: GateResult, diff: ReportDiff) -> None:
    with _tracer.start_as_current_span("eval.regression_gate") as span:
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
    with _tracer.start_as_current_span("eval.gate") as span:
        span.set_attribute("gate.passed", result.passed)
        span.set_attribute("gate.mean_score", result.actual_mean_score)
        span.set_attribute("gate.pass_rate", result.actual_pass_rate)
        if result.min_mean_score is not None:
            span.set_attribute("gate.min_mean_score", result.min_mean_score)
        if result.min_pass_rate is not None:
            span.set_attribute("gate.min_pass_rate", result.min_pass_rate)
    logger.info(
        "Eval gate evaluated",
        extra={
            "event": "eval_gate",
            "passed": result.passed,
            "mean_score": result.actual_mean_score,
            "pass_rate": result.actual_pass_rate,
            "errored": result.errored,
            "reasons": result.reasons,
        },
    )
