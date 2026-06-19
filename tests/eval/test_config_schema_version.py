"""Tests for EvalSettings additions: schema_version, gate thresholds, sinks."""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from mangomas.config import DEFAULT_EVAL_SCHEMA_VERSION, EvalSettings
from tests.constants import EVAL_SCHEMA_VERSION_CURRENT, EVAL_SINK_CONSOLE


def test_schema_version_defaults_to_current() -> None:
    assert EvalSettings().schema_version == EVAL_SCHEMA_VERSION_CURRENT
    assert EVAL_SCHEMA_VERSION_CURRENT == DEFAULT_EVAL_SCHEMA_VERSION


def test_future_schema_version_warns_not_raises(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="mangomas.config"):
        settings = EvalSettings(schema_version=DEFAULT_EVAL_SCHEMA_VERSION + 1)
    assert settings.schema_version == DEFAULT_EVAL_SCHEMA_VERSION + 1
    assert any(
        getattr(rec, "event", None) == "eval_config_future_version" for rec in caplog.records
    )


def test_current_schema_version_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="mangomas.config"):
        EvalSettings()
    assert not any(
        getattr(rec, "event", None) == "eval_config_future_version" for rec in caplog.records
    )


def test_gate_defaults_are_off() -> None:
    cfg = EvalSettings()
    assert cfg.gate_enabled is False
    assert cfg.min_mean_score is None
    assert cfg.min_pass_rate is None
    assert cfg.fail_on_error is False


def test_sinks_default_to_console_only() -> None:
    cfg = EvalSettings()
    assert cfg.sinks == [EVAL_SINK_CONSOLE]
    assert cfg.sink_options == {}


@pytest.mark.parametrize("field", ["min_mean_score", "min_pass_rate"])
def test_threshold_out_of_range_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        EvalSettings(**{field: 1.5})


@pytest.mark.parametrize("value", [0.0, 0.5, 1.0])
def test_threshold_within_range_accepted(value: float) -> None:
    assert EvalSettings(min_mean_score=value).min_mean_score == value
