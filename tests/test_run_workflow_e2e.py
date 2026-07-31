"""Tests for ``scripts/run_workflow_e2e.py`` — orchestrator teardown guarantees.

The script's ``_main`` must release the orchestrator (and with it the LLM
httpx pool) on *every* exit path: happy path, the tolerated
``MaxStepsExceeded`` outcome, and — the regression this file pins — any other
exception raised inside ``execute_workflow``.
"""

from __future__ import annotations

import pytest

from mangomas.errors import MaxStepsExceeded
from tests import constants
from tests._script_loader import load_script_module
from tests.fakes import FakeOrchestrator

e2e = load_script_module(constants.WORKFLOW_E2E_SCRIPT)


def _install(monkeypatch: pytest.MonkeyPatch, orch: FakeOrchestrator) -> None:
    """Route the script's ``build_orchestrator`` to the supplied fake."""
    monkeypatch.setattr(e2e, "build_orchestrator", lambda: orch)


async def test_main_closes_orchestrator_when_workflow_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generic ``execute_workflow`` failure must still await ``aclose``."""
    orch = FakeOrchestrator(raise_on_dispatch=RuntimeError(constants.WORKFLOW_E2E_FAILURE_MESSAGE))
    _install(monkeypatch, orch)

    with pytest.raises(RuntimeError, match=constants.WORKFLOW_E2E_FAILURE_MESSAGE):
        await e2e._main()

    assert orch.closed is True


async def test_main_returns_ok_and_closes_orchestrator_on_max_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``MaxStepsExceeded`` stays a tolerated outcome: exit 0 + ``aclose``."""
    orch = FakeOrchestrator(raise_on_dispatch=MaxStepsExceeded(steps=e2e._LOOP_MAX_STEPS))
    _install(monkeypatch, orch)

    exit_code = await e2e._main()

    assert exit_code == constants.WORKFLOW_E2E_EXIT_OK
    assert orch.closed is True


async def test_main_happy_path_reports_elapsed_and_closes_orchestrator(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The full graph succeeds, keeps the elapsed-time report, and closes."""
    orch = FakeOrchestrator()
    _install(monkeypatch, orch)

    exit_code = await e2e._main()

    assert exit_code == constants.WORKFLOW_E2E_EXIT_OK
    assert orch.closed is True
    assert constants.WORKFLOW_E2E_ELAPSED_MARKER in capsys.readouterr().out
