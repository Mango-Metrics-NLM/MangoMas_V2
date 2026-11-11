"""Judge calibration record (spec-0047 / reliability plan C2).

``llm_judge`` grades through :attr:`~mangomas.eval.protocol.ScorerContext.llm`,
the system-under-test's own client, so self-preference bias is structural.
Until a calibration record shows the judge agrees with expert labels, its
scores are **reported but excluded from the gate verdict**. This module is
the record's schema, its loader, and the pure assessment that turns a record
plus the acceptance policy into a :class:`JudgeStatus` the report carries.

Two claims the plan insists stay distinct: "self-preference not measured"
and "measured as unbiased". The record states which, the status repeats it,
and every sink prints it — neither blocks calibration, only
``measured_biased`` does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from mangomas.config import (
    DEFAULT_EVAL_JUDGE_CALIBRATION_MAX_AGE_DAYS,
    DEFAULT_EVAL_JUDGE_MIN_KAPPA,
    DEFAULT_EVAL_JUDGE_MIN_RHO,
    DEFAULT_EVAL_JUDGE_MIN_SAMPLE_SIZE,
)
from mangomas.errors import ConfigError
from mangomas.utils import clock

SelfPreference = Literal["not_measured", "measured_unbiased", "measured_biased"]

JUDGE_SCORER_NAME = "llm_judge"
NO_RECORD_REASON = "no calibration record"


class JudgeCalibrationRecord(BaseModel):
    """What a calibration run measured. Unknown keys are rejected, not ignored."""

    model_config = ConfigDict(extra="forbid")

    judge_model: str = Field(min_length=1)
    measured_at: date
    sample_size: int = Field(ge=1)
    cohens_kappa: float | None = Field(default=None, ge=-1.0, le=1.0, allow_inf_nan=False)
    spearman_rho: float | None = Field(default=None, ge=-1.0, le=1.0, allow_inf_nan=False)
    self_preference: SelfPreference = "not_measured"
    # spec-0048: digest of the judge prompt the agreement was measured under
    # (``JudgeStatus.prompt_sha256`` of the run that produced the labels). A
    # record without it, or for another prompt, does not calibrate.
    prompt_sha256: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    notes: str | None = None

    @model_validator(mode="after")
    def _at_least_one_agreement_statistic(self) -> JudgeCalibrationRecord:
        if self.cohens_kappa is None and self.spearman_rho is None:
            raise ValueError("a calibration record needs cohens_kappa or spearman_rho")
        return self


def load_calibration_record(path: str | Path) -> JudgeCalibrationRecord:
    """Read and validate a record; a set-but-wrong path is a configuration error."""
    file = Path(path)
    if not file.is_file():
        raise ConfigError(f"Judge calibration record not found: {file}")
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Malformed judge calibration record {file}: {exc}") from exc
    try:
        return JudgeCalibrationRecord.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid judge calibration record {file}: {exc}") from exc


@dataclass(frozen=True)
class CalibrationPolicy:
    """Acceptance thresholds; kappa **or** rho at or above its floor suffices."""

    min_sample_size: int = DEFAULT_EVAL_JUDGE_MIN_SAMPLE_SIZE
    min_kappa: float = DEFAULT_EVAL_JUDGE_MIN_KAPPA
    min_rho: float = DEFAULT_EVAL_JUDGE_MIN_RHO
    max_age_days: int = DEFAULT_EVAL_JUDGE_CALIBRATION_MAX_AGE_DAYS


@dataclass(frozen=True)
class JudgeStatus:
    """What the report says about its judge. JSON-safe (``dataclasses.asdict``)."""

    calibrated: bool
    reason: str
    scorer: str = JUDGE_SCORER_NAME
    judge_model: str | None = None
    measured_at: str | None = None
    sample_size: int | None = None
    cohens_kappa: float | None = None
    spearman_rho: float | None = None
    self_preference: str | None = None
    # spec-0048: the *current* judge prompt's digest (copy it into a record) and
    # the profile the judge grades through, if any.
    prompt_sha256: str | None = None
    judge_profile: str | None = None

    def summary(self) -> str:
        """One line for the gate note and the console: never drops the self-preference claim."""
        preference = self.self_preference or "not_measured"
        who = (
            f"{self.scorer} via profile {self.judge_profile}" if self.judge_profile else self.scorer
        )
        prompt = f"; prompt {short_digest(self.prompt_sha256)}" if self.prompt_sha256 else ""
        if not self.calibrated:
            return f"{who} uncalibrated ({self.reason}{prompt}; self-preference: {preference})"
        stats = [
            f"kappa={self.cohens_kappa:.2f}" if self.cohens_kappa is not None else None,
            f"rho={self.spearman_rho:.2f}" if self.spearman_rho is not None else None,
            f"n={self.sample_size}" if self.sample_size is not None else None,
            f"measured {self.measured_at}" if self.measured_at else None,
        ]
        detail = ", ".join(s for s in stats if s)
        return f"{who} calibrated ({detail}{prompt}; self-preference: {preference})"

    def as_child_of(self, parent: str) -> JudgeStatus:
        """The same status, labelled as *parent*'s child (composite scoring)."""
        return replace(self, scorer=f"{self.scorer} (child of {parent})")


#: Characters of the hex digest shown in human-facing lines.
_SHORT_DIGEST_HEX = 12


def short_digest(digest: str | None) -> str:
    """``sha256:`` + the first 12 hex characters; the JSON report keeps the full value."""
    if not digest:
        return "unknown"
    prefix, _, hexpart = digest.partition(":")
    return f"{prefix}:{hexpart[:_SHORT_DIGEST_HEX]}" if hexpart else digest[:_SHORT_DIGEST_HEX]


def uncalibrated(
    reason: str,
    *,
    judge_model: str | None = None,
    prompt_sha256: str | None = None,
    judge_profile: str | None = None,
) -> JudgeStatus:
    return JudgeStatus(
        calibrated=False,
        reason=reason,
        judge_model=judge_model,
        prompt_sha256=prompt_sha256,
        judge_profile=judge_profile,
    )


def assess_calibration(
    record: JudgeCalibrationRecord | None,
    *,
    judge_model: str | None,
    policy: CalibrationPolicy | None = None,
    now: datetime | None = None,
    prompt_sha256: str | None = None,
    judge_profile: str | None = None,
) -> JudgeStatus:
    """Pure: record + policy → status. The first failing rule names the reason.

    *prompt_sha256* is the digest of the judge prompt as configured now
    (spec-0048); a record must name the same digest, because a record measured
    under one prompt says nothing about another.
    """
    rules = policy or CalibrationPolicy()
    if record is None:
        return uncalibrated(
            NO_RECORD_REASON,
            judge_model=judge_model,
            prompt_sha256=prompt_sha256,
            judge_profile=judge_profile,
        )
    reason = _first_failing_rule(
        record, judge_model=judge_model, prompt_sha256=prompt_sha256, rules=rules, now=now
    )
    return JudgeStatus(
        calibrated=reason is None,
        reason=reason or "calibrated",
        # The model that is judging now; on a mismatch the reason names the
        # record's model (Copilot review on #126).
        judge_model=judge_model,
        measured_at=record.measured_at.isoformat(),
        sample_size=record.sample_size,
        cohens_kappa=record.cohens_kappa,
        spearman_rho=record.spearman_rho,
        self_preference=record.self_preference,
        prompt_sha256=prompt_sha256,
        judge_profile=judge_profile,
    )


def _first_failing_rule(
    record: JudgeCalibrationRecord,
    *,
    judge_model: str | None,
    prompt_sha256: str | None,
    rules: CalibrationPolicy,
    now: datetime | None,
) -> str | None:
    """Rules in precedence order; the first that fails is the reason."""
    model = (judge_model or "").strip()
    age_days = ((now or clock.now()).date() - record.measured_at).days
    kappa_ok = record.cohens_kappa is not None and record.cohens_kappa >= rules.min_kappa
    rho_ok = record.spearman_rho is not None and record.spearman_rho >= rules.min_rho
    checks: tuple[tuple[bool, str], ...] = (
        (not model, "judge model unknown"),
        (
            record.judge_model.strip() != model,
            f"record is for model {record.judge_model!r}, the judge runs {judge_model!r}",
        ),
        (
            record.prompt_sha256 is None,
            "record does not pin the judge prompt (prompt_sha256)",
        ),
        (
            record.prompt_sha256 != prompt_sha256,
            f"record is for judge prompt {short_digest(record.prompt_sha256)}, "
            f"the judge runs {short_digest(prompt_sha256)}",
        ),
        (age_days < 0, f"measured_at {record.measured_at.isoformat()} is in the future"),
        (
            age_days > rules.max_age_days,
            f"measured {age_days} days ago > max_age_days {rules.max_age_days}",
        ),
        (
            record.sample_size < rules.min_sample_size,
            f"sample_size {record.sample_size} < min_sample_size {rules.min_sample_size}",
        ),
        (record.self_preference == "measured_biased", "self-preference measured as biased"),
        (
            not (kappa_ok or rho_ok),
            f"kappa {_fmt(record.cohens_kappa)} < min_kappa {rules.min_kappa} and "
            f"rho {_fmt(record.spearman_rho)} < min_rho {rules.min_rho}",
        ),
    )
    return next((reason for failed, reason in checks if failed), None)


def _fmt(value: float | None) -> str:
    return "unmeasured" if value is None else f"{value:.2f}"


def status_summary_fields(status: JudgeStatus) -> dict[str, Any]:
    """Structured-log fields for a status (no free text beyond the reason)."""
    return {
        "judge_calibrated": status.calibrated,
        "judge_reason": status.reason,
        "judge_model": status.judge_model,
        "judge_self_preference": status.self_preference,
    }
