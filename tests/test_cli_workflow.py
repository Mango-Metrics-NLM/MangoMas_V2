"""Tests for the ``mangomas workflow`` CLI command (spec 0005 / ADR-0007).

Follows ``tests/test_cli.py``: ``_build`` is monkeypatched to a stub orchestrator
so the command never touches a real LLM or the filesystem.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mangomas.cli import main as cli_main
from mangomas.config import get_settings
from mangomas.core import AgentContext, AgentRequest, AgentResponse, Orchestrator
from tests.fakes import FakeLLM

_INLINE = json.dumps({"nodes": [{"id": "a", "agent": "up"}]})


class _EchoUpper:
    """Agent that upper-cases the last user message."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def handle(self, request: AgentRequest, _ctx: AgentContext) -> AgentResponse:
        return AgentResponse(content=request.messages[-1].content.upper(), agent=self.name)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _patched_orch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub ``_build`` with an orchestrator carrying a single ``up`` agent."""
    orch = Orchestrator(AgentContext(llm=FakeLLM(), repo=None))
    orch.register(_EchoUpper("up"))
    monkeypatch.setattr(cli_main, "_build", lambda: orch)

    async def _noop_close(_orch: Orchestrator) -> None:
        return None

    monkeypatch.setattr(cli_main, "_close_orchestrator", _noop_close)


def test_workflow_inline_json(runner: CliRunner) -> None:
    result = runner.invoke(cli_main.app, ["workflow", "hello", "--definition", _INLINE])
    assert result.exit_code == 0
    assert "HELLO" in result.stdout


def test_workflow_file_definition(runner: CliRunner, tmp_path: Path) -> None:
    path = tmp_path / "g.json"
    path.write_text(_INLINE, encoding="utf-8")
    result = runner.invoke(cli_main.app, ["workflow", "hi", "-f", str(path)])
    assert result.exit_code == 0
    assert "HI" in result.stdout


def test_workflow_from_settings(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_WORKFLOW__ENABLED", "true")
    monkeypatch.setenv("MANGOMAS_WORKFLOW__DEFINITION", _INLINE)
    get_settings.cache_clear()
    try:
        result = runner.invoke(cli_main.app, ["workflow", "yo"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 0
    assert "YO" in result.stdout


def test_workflow_no_definition_exits_2(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MANGOMAS_WORKFLOW__ENABLED", raising=False)
    monkeypatch.delenv("MANGOMAS_WORKFLOW__DEFINITION", raising=False)
    get_settings.cache_clear()
    try:
        result = runner.invoke(cli_main.app, ["workflow", "hi"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 2


def test_workflow_invalid_json_exits_2(runner: CliRunner) -> None:
    result = runner.invoke(cli_main.app, ["workflow", "hi", "-f", "{bad json"])
    assert result.exit_code == 2


def test_workflow_empty_definition_exits_2(runner: CliRunner) -> None:
    result = runner.invoke(cli_main.app, ["workflow", "hi", "-f", "   "])
    assert result.exit_code == 2


def test_workflow_unknown_agent_exits_1(runner: CliRunner) -> None:
    definition = json.dumps({"nodes": [{"id": "a", "agent": "ghost"}]})
    result = runner.invoke(cli_main.app, ["workflow", "hi", "-f", definition])
    assert result.exit_code == 1
