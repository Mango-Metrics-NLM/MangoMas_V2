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
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from opentelemetry import trace

from mangomas.config import DEFAULT_EVAL_TRIALS
from mangomas.errors import ConfigError
from mangomas.eval.judge_calibration import JudgeStatus
from mangomas.eval.provenance import RunProvenance
from mangomas.eval.runner import EvalReport, EvalRowResult, SliceStats
from mangomas.utils.json_path import finite_number

logger = logging.getLogger(__name__)

_ROW_FIELDS = {f.name for f in fields(EvalRowResult)}
_RUN_FIELDS = {f.name for f in fields(RunProvenance)}
_SLICE_FIELDS = {f.name for f in fields(SliceStats)}
_JUDGE_FIELDS = {f.name for f in fields(JudgeStatus)}


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
    with trace.get_tracer(__name__).start_as_current_span("eval.diff") as span:
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
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"Malformed baseline report {path}: {exc}") from exc
    return _report_from_dict(data)


def _require_finite_number(value: Any, *, field_name: str) -> float:
    result = finite_number(value)
    if result is None:
        raise ValueError(f"{field_name} must be a finite number; got {value!r}")
    return result


def _require_non_bool_int(value: Any, *, field_name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer; got {type(value).__name__}")
    if value < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}; got {value!r}")
    return value


def _require_optional_finite_number(value: Any, *, field_name: str) -> float | None:
    return None if value is None else _require_finite_number(value, field_name=field_name)


def _report_from_dict(data: Mapping[str, Any]) -> EvalReport:
    if not isinstance(data, Mapping):
        raise ConfigError(
            f"Invalid baseline report structure: expected mapping, got {type(data).__name__}"
        )
    try:
        rows = [_row_from_dict(r) for r in data["rows"]]
        raw_slices = data.get("slices")
        if raw_slices is None:
            slices: dict[str, SliceStats] = {}
        elif not isinstance(raw_slices, Mapping):
            raise TypeError(f"slices must be a mapping or null; got {type(raw_slices).__name__}")
        else:
            slices = {
                name: SliceStats(**{k: v for k, v in raw.items() if k in _SLICE_FIELDS})
                for name, raw in raw_slices.items()
            }
        return EvalReport(
            scorer=data["scorer"],
            agent_name=data["agent_name"],
            dataset_size=_require_non_bool_int(data["dataset_size"], field_name="dataset_size"),
            passed=_require_non_bool_int(data["passed"], field_name="passed"),
            failed=_require_non_bool_int(data["failed"], field_name="failed"),
            errored=_require_non_bool_int(data["errored"], field_name="errored"),
            mean_score=_require_finite_number(data["mean_score"], field_name="mean_score"),
            duration_ms=_require_finite_number(data["duration_ms"], field_name="duration_ms"),
            rows=rows,
            target_name=data.get("target_name", ""),
            mean_cost_usd=_require_optional_finite_number(
                data.get("mean_cost_usd"), field_name="mean_cost_usd"
            ),
            run=_run_from_dict(data.get("run")),
            slices=slices,
            trials=_require_non_bool_int(
                data.get("trials", DEFAULT_EVAL_TRIALS), field_name="trials", minimum=1
            ),
            pass_pow_k=_require_optional_finite_number(
                data.get("pass_pow_k"), field_name="pass_pow_k"
            ),
            trial_pass_rate=_require_optional_finite_number(
                data.get("trial_pass_rate"), field_name="trial_pass_rate"
            ),
            judge=_judge_from_dict(data.get("judge")),
        )
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        # ``AttributeError``: a row that is not an object (``.items`` on a list /
        # str); ``ValueError``: a scalar where the dataclass expects a type.
        raise ConfigError(f"Invalid baseline report structure: {exc}") from exc


def _run_from_dict(raw: Any) -> RunProvenance | None:
    """Provenance is a label block: absent in older baselines, tolerant of new keys."""
    if not isinstance(raw, Mapping):
        return None
    return RunProvenance(**{k: v for k, v in raw.items() if k in _RUN_FIELDS})


def _judge_from_dict(raw: Any) -> JudgeStatus | None:
    """Judge status (spec-0047): absent in older baselines, tolerant of new keys."""
    if not isinstance(raw, Mapping):
        return None
    return JudgeStatus(**{k: v for k, v in raw.items() if k in _JUDGE_FIELDS})


def _row_from_dict(raw: Mapping[str, Any]) -> EvalRowResult:
    # Filter to known fields so a newer artifact (extra columns) still loads.
    return EvalRowResult(**{k: v for k, v in raw.items() if k in _ROW_FIELDS})
