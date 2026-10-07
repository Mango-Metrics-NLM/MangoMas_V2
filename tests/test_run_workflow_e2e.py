"""Tests for ``scripts/run_workflow_e2e.py`` — orchestrator teardown guarantees.

The script's ``_main`` must release the orchestrator (and with it the LLM
httpx pool) on *every* exit path: happy path, the tolerated
``MaxStepsExceeded`` outcome, and — the regression this file pins — any other
exception raised inside ``execute_workflow``.
"""

from __future__ import annotations

import io
import logging
import sys

import pytest

from mangomas.config import AgentSettings, Settings
from mangomas.errors import MaxStepsExceeded
from mangomas.workflow.graph import LoopNode, SequenceNode
from tests import constants
from tests._script_loader import load_script_module
from tests.fakes import FakeOrchestrator

e2e = load_script_module(constants.WORKFLOW_E2E_SCRIPT)

# The codec Windows assigns a redirected stdout; it cannot encode the banner's
# arrows, which is exactly what crashed the script before any work ran (D5).
_LEGACY_CONSOLE_CODEC = "cp1252"
_NON_WINDOWS_PLATFORM = "linux"


def _legacy_stream(raw: io.BytesIO | None = None) -> io.TextIOWrapper:
    return io.TextIOWrapper(
        raw if raw is not None else io.BytesIO(), encoding=_LEGACY_CONSOLE_CODEC
    )


def _install(
    monkeypatch: pytest.MonkeyPatch,
    orch: FakeOrchestrator,
    seen: list[Settings | None] | None = None,
) -> None:
    """Route the script's ``build_orchestrator`` to the supplied fake.

    When *seen* is given, the ``Settings`` the script passed are recorded.
    """

    def _build(settings: Settings | None = None) -> FakeOrchestrator:
        if seen is not None:
            seen.append(settings)
        return orch

    monkeypatch.setattr(e2e, "build_orchestrator", _build)


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


# ── D5 [trunk]: cp1252 console (2026-10-06 AQA) ────────────────────────────────


async def test_main_survives_a_legacy_codec_stdout_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix, the banner raised ``UnicodeEncodeError`` on a redirected console."""
    raw_stdout = io.BytesIO()
    stdout, stderr = _legacy_stream(raw_stdout), _legacy_stream()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(sys, "platform", e2e._WINDOWS_PLATFORM)
    orch = FakeOrchestrator()
    _install(monkeypatch, orch)

    exit_code = await e2e._main()

    assert exit_code == constants.WORKFLOW_E2E_EXIT_OK
    assert orch.closed is True
    stdout.flush()
    written = raw_stdout.getvalue().decode(e2e._STDIO_ENCODING)
    assert "\u2192" in written
    assert constants.WORKFLOW_E2E_ELAPSED_MARKER in written


def test_ensure_utf8_stdio_reconfigures_on_windows() -> None:
    stream = _legacy_stream()
    e2e._ensure_utf8_stdio([stream], platform=e2e._WINDOWS_PLATFORM)
    assert stream.encoding == e2e._STDIO_ENCODING
    assert stream.errors == e2e._STDIO_ERRORS


def test_ensure_utf8_stdio_leaves_other_platforms_untouched() -> None:
    stream = _legacy_stream()
    e2e._ensure_utf8_stdio([stream], platform=_NON_WINDOWS_PLATFORM)
    assert stream.encoding == _LEGACY_CONSOLE_CODEC


def test_ensure_utf8_stdio_skips_streams_without_reconfigure() -> None:
    """An already-wrapped stream (no ``reconfigure``) is skipped, not crashed on."""
    e2e._ensure_utf8_stdio([object()], platform=e2e._WINDOWS_PLATFORM)


# ── L-T1: loop sentinel instruction (2026-10-07 live E2E) ─────────────────────
# Pre-fix, the loop agent only saw the fan-out's output (a ``sequence`` drops
# the user prompt), so live runs on two models always ended in MaxStepsExceeded.

_OPERATOR_PROMPT = "operator-owned prompt"
_OTHER_AGENT = "reviewer"
_LOOP_TEMPERATURE = 0.5


def _loop_agent_prompt(settings: Settings) -> str | None:
    return settings.agents[e2e._LOOP_AGENT].system_prompt


def test_graph_loop_iterates_the_instructed_agent_on_the_sentinel() -> None:
    """The agent given the instruction is the one the loop runs and accepts on."""
    graph = e2e.load_workflow(e2e.json.dumps(e2e._GRAPH))
    assert isinstance(graph.root, SequenceNode)
    loop = graph.root.steps[-1]
    assert isinstance(loop, LoopNode)
    assert loop.agent == e2e._LOOP_AGENT
    assert loop.accept.value == e2e._LOOP_SENTINEL
    assert e2e._LOOP_SENTINEL in e2e._LOOP_SYSTEM_PROMPT


def test_with_loop_instruction_sets_sentinel_prompt_when_unset() -> None:
    base = Settings(agents={})
    result = e2e._with_loop_instruction(base)
    assert _loop_agent_prompt(result) == e2e._LOOP_SYSTEM_PROMPT
    assert base.agents == {}, "the input settings must not be mutated"


def test_with_loop_instruction_preserves_other_fields_and_agents() -> None:
    base = Settings(
        agents={
            e2e._LOOP_AGENT: AgentSettings(temperature=_LOOP_TEMPERATURE),
            _OTHER_AGENT: AgentSettings(system_prompt=_OPERATOR_PROMPT),
        }
    )
    result = e2e._with_loop_instruction(base)
    assert _loop_agent_prompt(result) == e2e._LOOP_SYSTEM_PROMPT
    assert result.agents[e2e._LOOP_AGENT].temperature == _LOOP_TEMPERATURE
    assert result.agents[_OTHER_AGENT].system_prompt == _OPERATOR_PROMPT


def test_with_loop_instruction_respects_operator_prompt(
    caplog: pytest.LogCaptureFixture,
) -> None:
    base = Settings(agents={e2e._LOOP_AGENT: AgentSettings(system_prompt=_OPERATOR_PROMPT)})
    caplog.set_level(logging.INFO, logger=e2e.logger.name)
    result = e2e._with_loop_instruction(base)
    assert result is base
    assert _loop_agent_prompt(result) == _OPERATOR_PROMPT
    assert any(r.__dict__.get("agent") == e2e._LOOP_AGENT for r in caplog.records)


async def test_main_builds_orchestrator_with_loop_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_main`` must hand the instructed settings to the composition root."""
    orch = FakeOrchestrator()
    seen: list[Settings | None] = []
    _install(monkeypatch, orch, seen)

    await e2e._main()

    assert len(seen) == 1
    passed = seen[0]
    assert passed is not None
    assert passed.agents[e2e._LOOP_AGENT].system_prompt is not None
