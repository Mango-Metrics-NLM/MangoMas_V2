"""Tests for the Typer CLI (using a stub orchestrator built via monkeypatch)."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from mangomas.agents import ChatAgent
from mangomas.cli import main as cli_main
from mangomas.core import AgentContext, Orchestrator
from tests.fakes import FakeLLM


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _patch_build(monkeypatch: pytest.MonkeyPatch, orchestrator: Orchestrator) -> None:
    monkeypatch.setattr(cli_main, "_build", lambda: orchestrator)

    # In production each CLI invocation spawns a fresh process; the test
    # scaffold shares ONE orchestrator across multiple ``runner.invoke``
    # calls (so ``history`` can see the row ``chat`` persisted). Disable
    # the close path here to preserve that semantic — the close itself
    # is exercised by tests/test_cli_close.py instead.
    async def _noop_close(_orch: Orchestrator) -> None:
        return None

    monkeypatch.setattr(cli_main, "_close_orchestrator", _noop_close)


def test_agents_command(runner: CliRunner) -> None:
    result = runner.invoke(cli_main.app, ["agents"])
    assert result.exit_code == 0
    assert "chat" in result.stdout


def test_chat_command(runner: CliRunner) -> None:
    result = runner.invoke(cli_main.app, ["chat", "hello"])
    assert result.exit_code == 0
    assert "stub-reply" in result.stdout


def test_chat_command_with_system(runner: CliRunner) -> None:
    result = runner.invoke(
        cli_main.app, ["chat", "hello", "--system", "be brief", "--agent", "chat"]
    )
    assert result.exit_code == 0


def test_chat_command_verbose(runner: CliRunner) -> None:
    """--verbose flag sets logging to DEBUG without crashing."""
    result = runner.invoke(cli_main.app, ["chat", "hello", "--verbose"])
    assert result.exit_code == 0
    assert "stub-reply" in result.stdout


def test_history_command(runner: CliRunner) -> None:
    # First run a chat to produce a row.
    runner.invoke(cli_main.app, ["chat", "hello"])
    result = runner.invoke(cli_main.app, ["history", "--limit", "5"])
    assert result.exit_code == 0
    # At least one JSON line is printed.
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines
    parsed = json.loads(lines[0])
    assert parsed["agent"] == "chat"


def test_history_verbose(runner: CliRunner) -> None:
    runner.invoke(cli_main.app, ["chat", "hello"])
    result = runner.invoke(cli_main.app, ["history", "--verbose"])
    assert result.exit_code == 0


def test_history_no_repo_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    """history exits with code 1 when no repository is configured."""
    no_repo_orch = Orchestrator(AgentContext(llm=FakeLLM(), repo=None))
    no_repo_orch.register(ChatAgent())
    monkeypatch.setattr(cli_main, "_build", lambda: no_repo_orch)

    result = runner.invoke(cli_main.app, ["history"])
    assert result.exit_code == 1
    assert "No repository" in result.stdout or "No repository" in (result.stderr or "")
