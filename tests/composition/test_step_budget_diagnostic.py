"""Composition-root diagnostic: per-step budget vs LLM client timeout.

Live finding S1 (2026-10-07): with the shipped defaults the per-step budget
(``MANGOMAS_LOOP__STEP_TIMEOUT_SECONDS``) is below the LLM client timeout
(``MANGOMAS_LLM__TIMEOUT_SECONDS``). A slow completion is therefore cancelled
as ``StepTimeout`` (504) before the adapter's typed ``LLMTimeout`` can fire.
The defaults are left as is (they are documented and operator-owned); the
composition root now says so in a WARNING instead of leaving a bare 504.
"""

from __future__ import annotations

import logging

import pytest

from mangomas.composition import build_orchestrator
from mangomas.composition.builder import warn_if_step_budget_below_llm_timeout
from mangomas.config import (
    DEFAULT_LLM_TIMEOUT_SECONDS,
    DEFAULT_LOOP_STEP_TIMEOUT,
    LLMSettings,
    LoopSettings,
    Settings,
)
from tests.composition.helpers import close_repo

_BUILDER_LOGGER = "mangomas.composition.builder"
_EVENT = "step_budget_below_llm_timeout"


def _events(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "event", None) == _EVENT]


def test_shipped_defaults_are_inverted() -> None:
    """Pins the premise: if the defaults are ever realigned, revisit this diagnostic."""
    assert DEFAULT_LOOP_STEP_TIMEOUT < DEFAULT_LLM_TIMEOUT_SECONDS


def test_warns_when_step_budget_below_llm_timeout(caplog: pytest.LogCaptureFixture) -> None:
    loop = LoopSettings(step_timeout_seconds=DEFAULT_LLM_TIMEOUT_SECONDS / 2)
    llm = LLMSettings(timeout_seconds=DEFAULT_LLM_TIMEOUT_SECONDS)

    with caplog.at_level(logging.WARNING, logger=_BUILDER_LOGGER):
        emitted = warn_if_step_budget_below_llm_timeout(loop, llm)

    assert emitted is True
    (record,) = _events(caplog)
    assert record.levelno == logging.WARNING
    extras = record.__dict__
    assert extras["step_timeout_seconds"] == loop.step_timeout_seconds
    assert extras["llm_timeout_seconds"] == llm.timeout_seconds
    assert extras["llm_provider"] == llm.provider


@pytest.mark.parametrize("step_factor", [1.0, 2.0])
def test_silent_when_step_budget_covers_llm_timeout(
    caplog: pytest.LogCaptureFixture, step_factor: float
) -> None:
    """Equal budgets are fine: the LLM timeout fires first or simultaneously."""
    loop = LoopSettings(step_timeout_seconds=DEFAULT_LLM_TIMEOUT_SECONDS * step_factor)
    llm = LLMSettings(timeout_seconds=DEFAULT_LLM_TIMEOUT_SECONDS)

    with caplog.at_level(logging.WARNING, logger=_BUILDER_LOGGER):
        emitted = warn_if_step_budget_below_llm_timeout(loop, llm)

    assert emitted is False
    assert _events(caplog) == []


def test_build_orchestrator_emits_the_diagnostic_for_default_settings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Wiring: the composition root actually calls the check."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    with caplog.at_level(logging.WARNING, logger=_BUILDER_LOGGER):
        orch = build_orchestrator(settings)
    try:
        assert len(_events(caplog)) == 1
    finally:
        close_repo(orch)


def test_build_orchestrator_silent_when_budgets_aligned(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.loop.step_timeout_seconds = settings.llm.timeout_seconds
    with caplog.at_level(logging.WARNING, logger=_BUILDER_LOGGER):
        orch = build_orchestrator(settings)
    try:
        assert _events(caplog) == []
    finally:
        close_repo(orch)
