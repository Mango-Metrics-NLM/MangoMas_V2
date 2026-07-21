"""Tests for the ``mangomas workflow`` CLI sub-app (stub orchestrator)."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from mangomas.cli import main as cli_main
from mangomas.config import get_settings
from mangomas.core import Orchestrator
from tests.constants import STUB_REPLY, WORKFLOW_CONFIG_EXIT_CODE, WORKFLOW_RUNTIME_EXIT_CODE

_AGENT_GRAPH = json.dumps({"name": "t", "root": {"kind": "agent", "agent": "chat"}})
_GHOST_GRAPH = json.dumps({"name": "t", "root": {"kind": "agent", "agent": "ghost"}})


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _patch_build(monkeypatch: pytest.MonkeyPatch, orchestrator: Orchestrator) -> None:
    monkeypatch.setattr(cli_main, "_build", lambda: orchestrator)

    async def _noop_close(_orch: Orchestrator) -> None:
        return None

    monkeypatch.setattr(cli_main, "_close_orchestrator", _noop_close)
    # Feature is OFF by default in this process (no MANGOMAS_WORKFLOW__ env).
    get_settings.cache_clear()


def test_run_disabled_by_default_exits_config_code(runner: CliRunner) -> None:
    result = runner.invoke(cli_main.app, ["workflow", "run", "hi"])
    assert result.exit_code == WORKFLOW_CONFIG_EXIT_CODE


def test_run_with_inline_definition_prints_final_content(runner: CliRunner) -> None:
    result = runner.invoke(cli_main.app, ["workflow", "run", "hi", "--definition", _AGENT_GRAPH])
    assert result.exit_code == 0
    assert STUB_REPLY in result.stdout


def test_run_malformed_definition_exits_config_code(runner: CliRunner) -> None:
    result = runner.invoke(cli_main.app, ["workflow", "run", "hi", "-f", "{bad json"])
    assert result.exit_code == WORKFLOW_CONFIG_EXIT_CODE


def test_run_unknown_agent_exits_runtime_code(runner: CliRunner) -> None:
    result = runner.invoke(cli_main.app, ["workflow", "run", "hi", "-f", _GHOST_GRAPH])
    assert result.exit_code == WORKFLOW_RUNTIME_EXIT_CODE


def test_validate_ok(runner: CliRunner) -> None:
    result = runner.invoke(cli_main.app, ["workflow", "validate", "-f", _AGENT_GRAPH])
    assert result.exit_code == 0
    assert "ok" in result.stdout
    assert "name=t" in result.stdout


def test_validate_disabled_by_default_exits_config_code(runner: CliRunner) -> None:
    result = runner.invoke(cli_main.app, ["workflow", "validate"])
    assert result.exit_code == WORKFLOW_CONFIG_EXIT_CODE
