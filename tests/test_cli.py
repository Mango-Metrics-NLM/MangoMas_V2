"""Tests for the Typer CLI (using a stub orchestrator built via monkeypatch)."""

from __future__ import annotations

import json
import re

import pytest
from typer.testing import CliRunner

from mangomas.agents import ChatAgent
from mangomas.cli import _runtime as cli_runtime
from mangomas.cli import main as cli_main
from mangomas.composition import LLM_PROFILES_EXTRAS_KEY
from mangomas.core import AgentContext, Orchestrator
from mangomas.errors import ConfigError, LLMUnavailable
from tests._seam_guards import forbid_real_orchestrator
from tests.constants import (
    DEFAULT_API_HISTORY_MAX_LIMIT,
    EXIT_CONFIG_ERROR,
    EXIT_RUNTIME_ERROR,
    STUB_REPLY,
    TEST_LLM_PROFILE_NAME,
    UNKNOWN_LLM_PROFILE_NAME,
)
from tests.fakes import FakeLLM


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _patch_build(monkeypatch: pytest.MonkeyPatch, orchestrator: Orchestrator) -> None:
    forbid_real_orchestrator(monkeypatch)
    monkeypatch.setattr(cli_runtime, "_build", lambda: orchestrator)

    # In production each CLI invocation spawns a fresh process; the test
    # scaffold shares ONE orchestrator across multiple ``runner.invoke``
    # calls (so ``history`` can see the row ``chat`` persisted). Disable
    # the close path here to preserve that semantic — the close itself
    # is exercised by tests/test_cli_close.py instead.
    async def _noop_close(_orch: Orchestrator) -> None:
        return None

    monkeypatch.setattr(cli_runtime, "_close_orchestrator", _noop_close)


def test_agents_command(runner: CliRunner) -> None:
    result = runner.invoke(cli_main.app, ["agents"])
    assert result.exit_code == 0
    assert "chat" in result.stdout


def test_chat_command(runner: CliRunner) -> None:
    result = runner.invoke(cli_main.app, ["chat", "hello"])
    assert result.exit_code == 0
    assert STUB_REPLY in result.stdout


def test_chat_command_with_system(runner: CliRunner) -> None:
    result = runner.invoke(
        cli_main.app, ["chat", "hello", "--system", "be brief", "--agent", "chat"]
    )
    assert result.exit_code == 0


def test_chat_command_profile_routes_to_the_profile_client(
    runner: CliRunner, orchestrator: Orchestrator
) -> None:
    """`--profile` rides request.metadata like HTTP does (spec-0040 R2)."""
    profile_llm = FakeLLM(reply="profile-reply")
    orchestrator.context.extras[LLM_PROFILES_EXTRAS_KEY] = {TEST_LLM_PROFILE_NAME: profile_llm}
    result = runner.invoke(cli_main.app, ["chat", "hello", "--profile", TEST_LLM_PROFILE_NAME])
    assert result.exit_code == 0
    assert "profile-reply" in result.stdout
    assert profile_llm.calls


def test_chat_command_unknown_profile_fails(runner: CliRunner, orchestrator: Orchestrator) -> None:
    """The unknown profile surfaces as a typed ``ConfigError`` during dispatch, which
    ``run_or_exit`` reports as one line with the runtime exit code — not a traceback."""
    orchestrator.context.extras[LLM_PROFILES_EXTRAS_KEY] = {}
    result = runner.invoke(cli_main.app, ["chat", "hello", "--profile", UNKNOWN_LLM_PROFILE_NAME])
    assert result.exit_code == EXIT_RUNTIME_ERROR
    assert "Chat failed:" in result.output
    assert UNKNOWN_LLM_PROFILE_NAME in result.output
    assert not isinstance(result.exception, ConfigError)


def test_chat_command_verbose_requests_debug_logging(
    runner: CliRunner, cli_logging_calls: list[dict[str, object]]
) -> None:
    """`--verbose` asks for DEBUG logging, and the command still succeeds.

    The assertion on `cli_logging_calls` is the part that makes the name true.
    Without it this passed with the `if verbose:` branch deleted — the docstring
    used to claim "sets logging to DEBUG" while checking only an exit code.
    """
    result = runner.invoke(cli_main.app, ["chat", "hello", "--verbose"])
    assert result.exit_code == 0
    assert STUB_REPLY in result.stdout
    assert cli_logging_calls == [{"verbose": True}]


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


def test_history_verbose_requests_debug_logging(
    runner: CliRunner, cli_logging_calls: list[dict[str, object]]
) -> None:
    """`history --verbose` asks for DEBUG logging.

    Every command configures logging (so a non-verbose run still honours
    ``MANGOMAS_LOG_LEVEL`` / ``MANGOMAS_LOG__FORMAT`` and installs the
    correlation + trace filters), so the seeding `chat` invocation records
    ``verbose=False``. Asserting both entries in order is the negative half of
    the check: only the flagged invocation may request DEBUG.
    """
    runner.invoke(cli_main.app, ["chat", "hello"])
    result = runner.invoke(cli_main.app, ["history", "--verbose"])
    assert result.exit_code == 0
    assert cli_logging_calls == [{"verbose": False}, {"verbose": True}]


def test_history_no_repo_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    """history exits with code 1 when no repository is configured."""
    no_repo_orch = Orchestrator(AgentContext(llm=FakeLLM(), repo=None))
    no_repo_orch.register(ChatAgent())
    monkeypatch.setattr(cli_runtime, "_build", lambda: no_repo_orch)

    result = runner.invoke(cli_main.app, ["history"])
    assert result.exit_code == 1
    assert "No repository" in result.stdout or "No repository" in (result.stderr or "")


# ── Branch audit, Tier A ──────────────────────────────────────────────────────

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


@pytest.mark.parametrize("limit", ["0", "-1", str(DEFAULT_API_HISTORY_MAX_LIMIT + 1)])
def test_history_limit_is_bounded_like_the_api(runner: CliRunner, limit: str) -> None:
    """``--limit -1`` used to reach SQLite as ``LIMIT -1`` and dump every turn.
    Typer reports a usage error (exit 2) for an out-of-range option. Mutation
    (proved while writing): dropping ``min=1`` lets ``0`` and ``-1`` through."""
    result = runner.invoke(cli_main.app, ["history", "--limit", limit])
    assert result.exit_code == 2
    # Typer renders usage errors through Rich; under FORCE_COLOR (CI) the
    # option name arrives wrapped in ANSI style codes, so strip them first.
    assert "--limit" in _ANSI_ESCAPE_RE.sub("", result.output)


def test_agents_bootstraps_logging_like_its_siblings(
    runner: CliRunner, cli_logging_calls: list[dict[str, object]]
) -> None:
    result = runner.invoke(cli_main.app, ["agents"])
    assert result.exit_code == 0
    assert cli_logging_calls == [{}]


def test_chat_maps_a_composition_config_error_to_the_config_exit_code(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    """A bad MANGOMAS_* value used to escape ``chat`` as a traceback (exit 1).
    Mutation (proved while writing): narrowing ``build_or_exit``'s except."""

    def _boom() -> Orchestrator:
        raise ConfigError("unknown llm provider", detail="llm.provider='nope'")

    monkeypatch.setattr(cli_runtime, "_build", _boom)
    result = runner.invoke(cli_main.app, ["chat", "hi"])
    assert result.exit_code == EXIT_CONFIG_ERROR
    assert "Configuration error: unknown llm provider (llm.provider='nope')" in result.output
    assert "Traceback" not in result.output


def test_chat_maps_an_unreachable_llm_to_a_one_line_runtime_error(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    """LM Studio down used to print an ``LLMUnavailable`` traceback. The command
    now prints one line and exits with the runtime code. Mutation (proved while
    writing): reverting ``chat`` to a bare ``asyncio.run``."""
    down = LLMUnavailable("LM Studio unreachable", detail="ConnectError: refused")
    orch = Orchestrator(AgentContext(llm=FakeLLM(raise_on_complete=down), repo=None))
    orch.register(ChatAgent())
    monkeypatch.setattr(cli_runtime, "_build", lambda: orch)

    result = runner.invoke(cli_main.app, ["chat", "hi"])
    assert result.exit_code == EXIT_RUNTIME_ERROR
    assert "Chat failed: LM Studio unreachable (ConnectError: refused)" in result.output
    assert not isinstance(result.exception, LLMUnavailable)


def test_chat_maps_a_missing_optional_extra_to_the_config_exit_code(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    """``MANGOMAS_LLM__PROVIDER=vertex`` without the extra raises ``ImportError``
    from the adapter constructor, inside ``_build()``."""
    hint = "Vertex AI SDK is not installed. Install the optional extra: pip install 'x[y]'"

    def _missing_extra() -> Orchestrator:
        raise ImportError(hint)

    monkeypatch.setattr(cli_runtime, "_build", _missing_extra)
    result = runner.invoke(cli_main.app, ["chat", "hi"])
    assert result.exit_code == EXIT_CONFIG_ERROR
    assert f"Missing optional dependency: {hint}" in result.output
    assert not isinstance(result.exception, ImportError)
