"""Baseline loading + report diffing for regression gating.

A *baseline* is a previously-saved ``EvalReport`` JSON artifact (exactly what the
``json_file`` sink writes — ``dataclasses.asdict(report)``). :func:`load_baseline`
reconstructs the report (tolerating the sink's extra ``"gate"`` key and a missing
``target_name`` from pre-target-indirection artifacts). :func:`diff_reports` is a
pure function producing a :class:`ReportDiff` — per-metric deltas plus the
per-row partition (regressed / new / dropped) — with no I/O beyond a telemetry
span + structured log, mirroring :mod:`mangomas.eval.gate`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mangomas.errors import ConfigError
from mangomas.eval._serialize import report_payload
from mangomas.eval.runner import EvalReport, EvalRowResult
from mangomas.telemetry import get_tracer

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Mapping

logger = logging.getLogger(__name__)
_tracer = get_tracer(__name__)

_ROW_FIELDS = {f.name for f in fields(EvalRowResult)}


def _pass_rate(report: EvalReport) -> float:
    return report.passed / report.dataset_size if report.dataset_size else 0.0


def _is_clean_pass(row: EvalRowResult) -> bool:
    return row.passed and row.error is None


@dataclass(frozen=True)
class ReportDiff:
    """Difference between a baseline and a current :class:`EvalReport`.

    Deltas are ``current - baseline`` (negative = regression). ``regressed_rows``
    are row ids that were a clean pass in the baseline but are not in the current
    run; ``new_rows`` / ``dropped_rows`` are ids present in only one report.
    """

    baseline_mean_score: float
    current_mean_score: float
    mean_score_delta: float
    baseline_pass_rate: float
    current_pass_rate: float
    pass_rate_delta: float
    passed_delta: int
    failed_delta: int
    errored_delta: int
    regressed_rows: list[str] = field(default_factory=list)
    new_rows: list[str] = field(default_factory=list)
    dropped_rows: list[str] = field(default_factory=list)


def diff_reports(baseline: EvalReport, current: EvalReport) -> ReportDiff:
    """Return a pure :class:`ReportDiff` of *current* against *baseline*."""
    base_rate = _pass_rate(baseline)
    cur_rate = _pass_rate(current)
    base_by_id = {r.row_id: r for r in baseline.rows}
    cur_by_id = {r.row_id: r for r in current.rows}
    shared = base_by_id.keys() & cur_by_id.keys()
    regressed = sorted(
        rid
        for rid in shared
        if _is_clean_pass(base_by_id[rid]) and not _is_clean_pass(cur_by_id[rid])
    )
    new_rows = sorted(cur_by_id.keys() - base_by_id.keys())
    dropped_rows = sorted(base_by_id.keys() - cur_by_id.keys())
    diff = ReportDiff(
        baseline_mean_score=baseline.mean_score,
        current_mean_score=current.mean_score,
        mean_score_delta=current.mean_score - baseline.mean_score,
        baseline_pass_rate=base_rate,
        current_pass_rate=cur_rate,
        pass_rate_delta=cur_rate - base_rate,
        passed_delta=current.passed - baseline.passed,
        failed_delta=current.failed - baseline.failed,
        errored_delta=current.errored - baseline.errored,
        regressed_rows=regressed,
        new_rows=new_rows,
        dropped_rows=dropped_rows,
    )
    _log_diff(diff)
    return diff


def _log_diff(diff: ReportDiff) -> None:
    with _tracer.start_as_current_span("eval.diff") as span:
        span.set_attribute("diff.mean_score_delta", diff.mean_score_delta)
        span.set_attribute("diff.pass_rate_delta", diff.pass_rate_delta)
        span.set_attribute("diff.regressed_count", len(diff.regressed_rows))
    logger.info(
        "Eval baseline diff computed",
        extra={
            "event": "eval_diff",
            "mean_score_delta": diff.mean_score_delta,
            "pass_rate_delta": diff.pass_rate_delta,
            "regressed": len(diff.regressed_rows),
            "new": len(diff.new_rows),
            "dropped": len(diff.dropped_rows),
        },
    )


async def load_baseline(path: Path | str) -> EvalReport:
    """Load a baseline ``EvalReport`` from a ``json_file``-sink artifact.

    Raises :class:`ConfigError` (→ CLI exit 2) when the file is missing or
    malformed. Unknown top-level keys (e.g. the sink's ``"gate"``) are ignored
    and a missing ``target_name`` defaults to ``""``.
    """
    return await asyncio.to_thread(_read_baseline, Path(path))


def _read_baseline(path: Path) -> EvalReport:
    if not path.is_file():
        raise ConfigError(f"Baseline report not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ConfigError(f"Malformed baseline report {path}: {exc}") from exc
    return _report_from_dict(data)


def _report_from_dict(data: Mapping[str, Any]) -> EvalReport:
    try:
        rows = [_row_from_dict(r) for r in data["rows"]]
        return EvalReport(
            scorer=data["scorer"],
            agent_name=data["agent_name"],
            dataset_size=data["dataset_size"],
            passed=data["passed"],
            failed=data["failed"],
            errored=data["errored"],
            mean_score=data["mean_score"],
            duration_ms=data["duration_ms"],
            rows=rows,
            target_name=data.get("target_name", ""),
        )
    except (KeyError, TypeError) as exc:
        raise ConfigError(f"Invalid baseline report structure: {exc}") from exc


def _row_from_dict(raw: Mapping[str, Any]) -> EvalRowResult:
    # Filter to known fields so a newer artifact (extra columns) still loads.
    return EvalRowResult(**{k: v for k, v in raw.items() if k in _ROW_FIELDS})


def report_to_dict(report: EvalReport) -> dict[str, Any]:
    """Serialise *report* to the same dict shape the ``json_file`` sink writes."""
    return report_payload(report)
