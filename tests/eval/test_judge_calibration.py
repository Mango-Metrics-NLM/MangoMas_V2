"""Judge calibration record and assessment (spec-0047 / reliability plan C2).

Every rule in ``assess_calibration`` is pinned in both directions, and the two
self-preference claims the plan keeps apart — "not measured" and "measured as
unbiased" — both calibrate while "measured as biased" never does.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from mangomas.errors import ConfigError
from mangomas.eval.judge_calibration import (
    NO_RECORD_REASON,
    CalibrationPolicy,
    JudgeCalibrationRecord,
    JudgeStatus,
    assess_calibration,
    load_calibration_record,
    short_digest,
    status_summary_fields,
)
from mangomas.utils import clock

_MODEL = "judge-model-v1"
_PROMPT = "sha256:" + "a" * 64
_OTHER_PROMPT = "sha256:" + "b" * 64
_NOW = datetime(2026, 11, 10, tzinfo=UTC)
_POLICY = CalibrationPolicy(min_sample_size=200, min_kappa=0.8, min_rho=0.85, max_age_days=180)


def _record(**overrides: object) -> JudgeCalibrationRecord:
    base: dict[str, object] = {
        "judge_model": _MODEL,
        "measured_at": "2026-10-01",
        "sample_size": 300,
        "cohens_kappa": 0.86,
        "spearman_rho": None,
        "self_preference": "not_measured",
        "prompt_sha256": _PROMPT,
    }
    base.update(overrides)
    return JudgeCalibrationRecord.model_validate(base)


def _assess(record: JudgeCalibrationRecord | None, **kwargs: object) -> JudgeStatus:
    params: dict[str, object] = {
        "judge_model": _MODEL,
        "policy": _POLICY,
        "now": _NOW,
        "prompt_sha256": _PROMPT,
    }
    params.update(kwargs)
    return assess_calibration(record, **params)  # type: ignore[arg-type]


def test_no_record_is_uncalibrated_with_the_named_reason() -> None:
    status = _assess(None)
    assert (status.calibrated, status.reason, status.judge_model) == (
        False,
        NO_RECORD_REASON,
        _MODEL,
    )
    assert (
        "uncalibrated" in status.summary() and "self-preference: not_measured" in status.summary()
    )


def test_a_sound_record_calibrates_and_keeps_the_self_preference_claim() -> None:
    not_measured = _assess(_record())
    assert not_measured.calibrated is True and not_measured.reason == "calibrated"
    assert not_measured.self_preference == "not_measured"
    assert "kappa=0.86" in not_measured.summary() and "n=300" in not_measured.summary()
    assert "self-preference: not_measured" in not_measured.summary()
    unbiased = _assess(_record(self_preference="measured_unbiased"))
    assert unbiased.calibrated is True
    assert "self-preference: measured_unbiased" in unbiased.summary()
    assert not_measured.summary() != unbiased.summary(), "the two claims never read the same"


@pytest.mark.parametrize(
    ("overrides", "kwargs", "fragment"),
    [
        ({}, {"judge_model": None}, "judge model unknown"),
        ({}, {"judge_model": "   "}, "judge model unknown"),
        ({"judge_model": "other"}, {}, "record is for model 'other'"),
        ({"measured_at": "2026-12-01"}, {}, "in the future"),
        ({"measured_at": "2026-01-01"}, {}, "days ago > max_age_days 180"),
        ({"sample_size": 50}, {}, "sample_size 50 < min_sample_size 200"),
        ({"self_preference": "measured_biased"}, {}, "measured as biased"),
        ({"cohens_kappa": 0.5, "spearman_rho": 0.5}, {}, "kappa 0.50 < min_kappa 0.8 and rho 0.50"),
        ({"cohens_kappa": 0.5, "spearman_rho": None}, {}, "rho unmeasured < min_rho"),
    ],
)
def test_each_rule_fails_with_its_own_reason(
    overrides: dict[str, object], kwargs: dict[str, object], fragment: str
) -> None:
    status = _assess(_record(**overrides), **kwargs)
    assert status.calibrated is False
    assert fragment in status.reason, status.reason


def test_kappa_or_rho_alone_suffices() -> None:
    assert _assess(_record(cohens_kappa=None, spearman_rho=0.9)).calibrated is True
    assert _assess(_record(cohens_kappa=0.81, spearman_rho=0.1)).calibrated is True
    assert _assess(_record(cohens_kappa=0.5, spearman_rho=0.86)).calibrated is True


def test_boundaries_are_inclusive_and_the_clock_seam_is_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at_age_limit = _record(
        measured_at=(_NOW.date() - __import__("datetime").timedelta(days=180)).isoformat()
    )
    assert _assess(at_age_limit).calibrated is True
    assert _assess(_record(sample_size=200, cohens_kappa=0.8)).calibrated is True
    monkeypatch.setattr(clock, "now", lambda: _NOW)
    assert (
        assess_calibration(
            _record(), judge_model=_MODEL, policy=_POLICY, prompt_sha256=_PROMPT
        ).calibrated
        is True
    )
    default_policy = assess_calibration(_record(), judge_model=_MODEL, prompt_sha256=_PROMPT)
    assert default_policy.calibrated is True, "default policy"


def test_record_schema_rejects_bad_shapes() -> None:
    with pytest.raises(ValueError, match="cohens_kappa or spearman_rho"):
        _record(cohens_kappa=None, spearman_rho=None)
    for bad in (
        {"cohens_kappa": 1.5},
        {"sample_size": 0},
        {"extra": 1},
        {"self_preference": "maybe"},
    ):
        with pytest.raises(ValueError):
            _record(**bad)


def test_load_calibration_record_errors_are_config_errors(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    with pytest.raises(ConfigError, match="not found"):
        load_calibration_record(missing)
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="Malformed"):
        load_calibration_record(broken)
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"judge_model": _MODEL}), encoding="utf-8")
    with pytest.raises(ConfigError, match="Invalid"):
        load_calibration_record(invalid)
    good = tmp_path / "good.json"
    good.write_text(_record().model_dump_json(), encoding="utf-8")
    assert load_calibration_record(str(good)).measured_at == date(2026, 10, 1)


def test_status_is_json_safe_and_relabels_as_a_child() -> None:
    status = _assess(_record())
    payload = json.loads(json.dumps(dataclasses.asdict(status)))
    assert payload["measured_at"] == "2026-10-01" and payload["calibrated"] is True
    child = status.as_child_of("composite")
    assert child.scorer == "llm_judge (child of composite)" and child.calibrated is True
    assert status_summary_fields(child)["judge_calibrated"] is True


# --- calibration pinned to the judge prompt (spec-0048) ----------------------


def test_a_record_for_another_prompt_does_not_calibrate() -> None:
    """A record measured under one prompt says nothing about another."""
    status = _assess(_record(), prompt_sha256=_OTHER_PROMPT)
    assert status.calibrated is False
    assert "record is for judge prompt sha256:aaaaaaaaaaaa" in status.reason
    assert "the judge runs sha256:bbbbbbbbbbbb" in status.reason
    assert status.prompt_sha256 == _OTHER_PROMPT, "the status carries the current digest"


def test_a_record_that_does_not_pin_a_prompt_does_not_calibrate() -> None:
    status = _assess(_record(prompt_sha256=None))
    assert status.calibrated is False
    assert status.reason == "record does not pin the judge prompt (prompt_sha256)"
    with pytest.raises(ValueError, match="prompt_sha256"):
        _record(prompt_sha256="md5:abc")


def test_the_summary_names_the_profile_and_the_short_prompt_digest() -> None:
    status = _assess(_record(), judge_profile="cloud")
    assert status.calibrated is True and status.judge_profile == "cloud"
    summary = status.summary()
    assert summary.startswith("llm_judge via profile cloud calibrated (")
    assert "prompt sha256:aaaaaaaaaaaa" in summary
    missing = _assess(None, judge_profile="cloud")
    assert missing.summary().startswith("llm_judge via profile cloud uncalibrated (")
    assert short_digest(None) == "unknown"
    assert short_digest("opaque-digest-value") == "opaque-diges"


def test_a_model_mismatch_reports_the_active_judge_model() -> None:
    """Copilot review on #126: the status names who judged, the reason names the record."""
    status = _assess(_record(judge_model="recorded-model"), judge_model="active-model")
    assert status.calibrated is False
    assert status.judge_model == "active-model"
    assert "record is for model 'recorded-model'" in status.reason
    assert _assess(_record(), judge_model=None).judge_model is None
