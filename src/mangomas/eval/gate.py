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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from mangomas.telemetry import get_tracer

if TYPE_CHECKING:  # pragma: no cover
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
    """

    passed: bool
    actual_mean_score: float
    actual_pass_rate: float
    min_mean_score: float | None = None
    min_pass_rate: float | None = None
    fail_on_error: bool = False
    errored: int = 0
    reasons: list[str] = field(default_factory=list)


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
        reasons.append("empty dataset — nothing to gate")
        result = GateResult(
            passed=True,
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

    if min_mean_score is not None and report.mean_score < min_mean_score:
        reasons.append(f"mean_score {report.mean_score:.3f} < min_mean_score {min_mean_score:.3f}")
    if min_pass_rate is not None and pass_rate < min_pass_rate:
        reasons.append(f"pass_rate {pass_rate:.3f} < min_pass_rate {min_pass_rate:.3f}")
    if fail_on_error and report.errored > 0:
        reasons.append(f"{report.errored} row(s) errored (fail_on_error)")

    result = GateResult(
        passed=not reasons,
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
