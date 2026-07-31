"""Tests for ToolAgent: tool dispatch, error mapping, and loop behaviour."""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

import pytest

from mangomas.agents.tool_agent import ToolAgent
from mangomas.config import DEFAULT_TOOL_MAX_STEPS, AgentSettings, Settings
from mangomas.core.agent import AgentContext, AgentRequest, Message
from mangomas.core.tools import ToolRegistry, build_tool_system_prompt
from mangomas.errors import ToolExecutionError, ToolNotFound
from mangomas.registry import Registry
from tests.constants import (
    DEFAULT_TOOL_NAME,
    TEST_TOOL_MAX_STEPS,
    TEST_TOOL_MAX_STEPS_OVERRIDE,
    TEST_TOOL_SYSTEM_PROMPT,
    TOOL_MAX_STEPS_ENV,
)
from tests.fakes import FakeLLM, FakeTool

MessageRole = Literal["system", "user", "assistant", "tool"]


def _tool_registry(tool: FakeTool | None = None) -> ToolRegistry:
    reg: ToolRegistry = Registry("tool")
    t = tool or FakeTool()
    reg.register(t.name, t)
    return reg


def _tool_reply() -> str:
    """A well-formed tool-call reply targeting the default fake tool."""
    tool_json = json.dumps({"tool": DEFAULT_TOOL_NAME, "arguments": {}})
    return f"```json\n{tool_json}\n```"


def _ctx(
    llm: FakeLLM | None = None,
    tools: ToolRegistry | None = None,
) -> AgentContext:
    return AgentContext(llm=llm or FakeLLM(), repo=None, tools=tools)


def _req(*msgs: tuple[MessageRole, str]) -> AgentRequest:
    return AgentRequest(messages=[Message(role=r, content=c) for r, c in msgs])


# ── Plain response (no tool call) ─────────────────────────────────────────────


async def test_tool_agent_plain_response() -> None:
    llm = FakeLLM(reply="Hello!")
    agent = ToolAgent()
    resp = await agent.handle(_req(("user", "hi")), _ctx(llm=llm))
    assert resp.content == "Hello!"
    assert resp.agent == "tool"


# ── Single tool call then final response ──────────────────────────────────────


async def test_tool_agent_executes_single_tool_call() -> None:
    tool_json = json.dumps({"tool": DEFAULT_TOOL_NAME, "arguments": {"msg": "ping"}})
    llm = FakeLLM(replies=[f"```json\n{tool_json}\n```", "Final answer."])
    tool = FakeTool()
    reg = _tool_registry(tool)
    agent = ToolAgent()
    resp = await agent.handle(_req(("user", "go")), _ctx(llm=llm, tools=reg))
    assert resp.content == "Final answer."
    assert len(tool.calls) == 1
    assert tool.calls[0] == {"msg": "ping"}


# ── Tool result re-injected as 'tool' role message ────────────────────────────


async def test_tool_agent_reinjects_tool_result() -> None:
    tool_json = json.dumps({"tool": DEFAULT_TOOL_NAME, "arguments": {}})
    llm = FakeLLM(replies=[f"```json\n{tool_json}\n```", "Done."])
    tool = FakeTool(result="pong-value")
    reg = _tool_registry(tool)
    agent = ToolAgent()
    await agent.handle(_req(("user", "go")), _ctx(llm=llm, tools=reg))
    # Second call should contain a "tool" role message with the result.
    second_call = llm.calls[1]
    tool_msgs = [m for m in second_call if m.role == "tool"]
    assert len(tool_msgs) == 1
    data = json.loads(tool_msgs[0].content)
    assert data["output"] == "pong-value"


# ── Unknown tool raises ToolNotFound ──────────────────────────────────────────


async def test_tool_agent_unknown_tool_raises_tool_not_found() -> None:
    tool_json = json.dumps({"tool": "ghost", "arguments": {}})
    llm = FakeLLM(reply=f"```json\n{tool_json}\n```")
    reg = _tool_registry()  # only has DEFAULT_TOOL_NAME
    agent = ToolAgent()
    with pytest.raises(ToolNotFound) as exc_info:
        await agent.handle(_req(("user", "go")), _ctx(llm=llm, tools=reg))
    assert exc_info.value.name == "ghost"


async def test_tool_agent_unknown_tool_logs_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tool_json = json.dumps({"tool": "ghost", "arguments": {}})
    llm = FakeLLM(reply=f"```json\n{tool_json}\n```")
    reg = _tool_registry()
    agent = ToolAgent()
    with (
        caplog.at_level(logging.ERROR, logger="mangomas.agents.tool_agent"),
        pytest.raises(ToolNotFound),
    ):
        await agent.handle(_req(("user", "go")), _ctx(llm=llm, tools=reg))
    assert "unknown tool" in caplog.text


async def test_tool_agent_tool_call_without_registry_logs_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tool_json = json.dumps({"tool": DEFAULT_TOOL_NAME, "arguments": {}})
    llm = FakeLLM(reply=f"```json\n{tool_json}\n```")
    agent = ToolAgent()
    with (
        caplog.at_level(logging.ERROR, logger="mangomas.agents.tool_agent"),
        pytest.raises(ToolNotFound),
    ):
        await agent.handle(_req(("user", "go")), _ctx(llm=llm, tools=None))
    assert "no tool registry" in caplog.text


# ── Tool execution error wraps non-ToolExecutionError exceptions ───────────────


async def test_tool_agent_execution_failure_raises_tool_execution_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenTool(FakeTool):
        async def execute(self, _arguments: dict[str, Any]) -> str:
            raise RuntimeError("disk full")

    tool_json = json.dumps({"tool": DEFAULT_TOOL_NAME, "arguments": {}})
    llm = FakeLLM(reply=f"```json\n{tool_json}\n```")
    broken = BrokenTool(name=DEFAULT_TOOL_NAME)
    reg: ToolRegistry = Registry("tool")
    reg.register(DEFAULT_TOOL_NAME, broken)
    agent = ToolAgent()
    with (
        caplog.at_level(logging.ERROR, logger="mangomas.agents.tool_agent"),
        pytest.raises(ToolExecutionError) as exc_info,
    ):
        await agent.handle(_req(("user", "go")), _ctx(llm=llm, tools=reg))
    assert exc_info.value.tool_name == DEFAULT_TOOL_NAME
    assert "Tool execution failed" in caplog.text


# ── ToolExecutionError is not re-wrapped ──────────────────────────────────────


async def test_tool_execution_error_propagates_unchanged() -> None:
    class AlreadyError(FakeTool):
        async def execute(self, _arguments: dict[str, Any]) -> str:
            raise ToolExecutionError("already", tool_name=self.name)

    tool_json = json.dumps({"tool": DEFAULT_TOOL_NAME, "arguments": {}})
    llm = FakeLLM(reply=f"```json\n{tool_json}\n```")
    bad = AlreadyError(name=DEFAULT_TOOL_NAME)
    reg: ToolRegistry = Registry("tool")
    reg.register(DEFAULT_TOOL_NAME, bad)
    agent = ToolAgent()
    with pytest.raises(ToolExecutionError) as exc_info:
        await agent.handle(_req(("user", "go")), _ctx(llm=llm, tools=reg))
    assert str(exc_info.value) == "already"


# ── max_tool_steps exhausted: budget bounds total LLM calls ───────────────────


async def test_tool_agent_exhausts_max_tool_steps() -> None:
    tool_reply = _tool_reply()
    llm = FakeLLM(reply=tool_reply)  # keeps requesting tools forever
    tool = FakeTool()
    reg = _tool_registry(tool)
    agent = ToolAgent(max_tool_steps=TEST_TOOL_MAX_STEPS)
    resp = await agent.handle(_req(("user", "go")), _ctx(llm=llm, tools=reg))
    # Exactly N LLM calls — no extra "final" call beyond the budget.
    assert len(llm.calls) == TEST_TOOL_MAX_STEPS
    assert resp.metadata["tool_steps"] == TEST_TOOL_MAX_STEPS
    # The last reply is returned as-is; its tool call is not executed.
    assert resp.content == tool_reply
    assert len(tool.calls) == TEST_TOOL_MAX_STEPS - 1


# ── No tools registered: no system prompt injection ───────────────────────────


async def test_tool_agent_no_tools_no_tool_prompt() -> None:
    llm = FakeLLM(reply="ok")
    agent = ToolAgent()
    resp = await agent.handle(_req(("user", "hi")), _ctx(llm=llm, tools=None))
    assert resp.content == "ok"
    # Only the user message should be in the first call.
    assert llm.calls[0] == [Message(role="user", content="hi")]


# ── Custom system_prompt injected before user messages ────────────────────────


async def test_tool_agent_custom_system_prompt_injected() -> None:
    llm = FakeLLM(reply="ok")
    agent = ToolAgent(system_prompt="Be concise.")
    await agent.handle(_req(("user", "hi")), _ctx(llm=llm))
    first_message = llm.calls[0][0]
    assert first_message.role == "system"
    assert first_message.content == "Be concise."


# ── System prompt not duplicated when already present ─────────────────────────


async def test_tool_agent_no_duplicate_system_prompt() -> None:
    llm = FakeLLM(reply="ok")
    agent = ToolAgent(system_prompt="Injected.")
    req = AgentRequest(
        messages=[
            Message(role="system", content="Existing system."),
            Message(role="user", content="hi"),
        ]
    )
    await agent.handle(req, _ctx(llm=llm))
    system_msgs = [m for m in llm.calls[0] if m.role == "system"]
    assert len(system_msgs) == 1
    assert system_msgs[0].content == "Existing system."


# ── Custom system prompt + tools: BOTH prompts sent, custom first ─────────────


@pytest.mark.asyncio
async def test_tool_agent_combines_custom_and_tool_prompt() -> None:
    llm = FakeLLM(reply="ok")
    tool = FakeTool()
    reg = _tool_registry(tool)
    agent = ToolAgent(system_prompt=TEST_TOOL_SYSTEM_PROMPT)
    await agent.handle(_req(("user", "hi")), _ctx(llm=llm, tools=reg))
    first_message = llm.calls[0][0]
    assert first_message.role == "system"
    expected_tool_prompt = build_tool_system_prompt([tool.spec])
    assert first_message.content == f"{TEST_TOOL_SYSTEM_PROMPT}\n\n{expected_tool_prompt}"


@pytest.mark.asyncio
async def test_tool_agent_tool_prompt_alone_without_custom_prompt() -> None:
    llm = FakeLLM(reply="ok")
    tool = FakeTool()
    reg = _tool_registry(tool)
    agent = ToolAgent()
    await agent.handle(_req(("user", "hi")), _ctx(llm=llm, tools=reg))
    first_message = llm.calls[0][0]
    assert first_message.role == "system"
    assert first_message.content == build_tool_system_prompt([tool.spec])


# ── Blank/whitespace prompts are normalized to None ────────────────────────────


@pytest.mark.asyncio
async def test_tool_agent_blank_custom_prompt_no_tools() -> None:
    """Empty string custom prompt with no tools should not inject a system message."""
    llm = FakeLLM(reply="ok")
    agent = ToolAgent(system_prompt="")
    await agent.handle(_req(("user", "hi")), _ctx(llm=llm))
    system_msgs = [m for m in llm.calls[0] if m.role == "system"]
    assert len(system_msgs) == 0


@pytest.mark.asyncio
async def test_tool_agent_whitespace_prompt_with_tools() -> None:
    """Whitespace-only custom prompt with tools should use only the tool prompt."""
    llm = FakeLLM(reply="ok")
    tool = FakeTool()
    reg = _tool_registry(tool)
    agent = ToolAgent(system_prompt="   \t  \n  ")
    await agent.handle(_req(("user", "hi")), _ctx(llm=llm, tools=reg))
    first_message = llm.calls[0][0]
    assert first_message.role == "system"
    # Should be just the tool prompt, not prefixed with whitespace
    assert first_message.content == build_tool_system_prompt([tool.spec])


@pytest.mark.asyncio
async def test_tool_agent_blank_prompt_no_duplicate_prefix() -> None:
    """Blank custom prompt + tools should not produce '\\n\\n' prefix."""
    llm = FakeLLM(reply="ok")
    tool = FakeTool()
    reg = _tool_registry(tool)
    agent = ToolAgent(system_prompt="")
    await agent.handle(_req(("user", "hi")), _ctx(llm=llm, tools=reg))
    first_message = llm.calls[0][0]
    assert first_message.role == "system"
    # Should not start with \n\n
    assert not first_message.content.startswith("\n\n")
    assert first_message.content == build_tool_system_prompt([tool.spec])


# ── metadata["tool_steps"] == actual number of LLM calls ──────────────────────


@pytest.mark.asyncio
async def test_tool_agent_prose_immediately_counts_one_llm_call() -> None:
    llm = FakeLLM(reply="Just prose.")
    reg = _tool_registry()
    agent = ToolAgent()
    resp = await agent.handle(_req(("user", "hi")), _ctx(llm=llm, tools=reg))
    assert len(llm.calls) == 1
    assert resp.metadata["tool_steps"] == 1


@pytest.mark.asyncio
async def test_tool_agent_tool_then_prose_counts_llm_calls() -> None:
    llm = FakeLLM(replies=[_tool_reply(), "Final answer."])
    tool = FakeTool()
    reg = _tool_registry(tool)
    agent = ToolAgent()
    resp = await agent.handle(_req(("user", "go")), _ctx(llm=llm, tools=reg))
    assert resp.content == "Final answer."
    assert len(llm.calls) == 2
    assert resp.metadata["tool_steps"] == 2
    assert len(tool.calls) == 1


@pytest.mark.asyncio
async def test_tool_agent_zero_budget_makes_no_llm_calls() -> None:
    llm = FakeLLM(reply=_tool_reply())
    reg = _tool_registry()
    agent = ToolAgent(max_tool_steps=0)
    resp = await agent.handle(_req(("user", "go")), _ctx(llm=llm, tools=reg))
    assert llm.calls == []
    assert resp.content == ""
    assert resp.metadata["tool_steps"] == 0


# ── max_tool_steps resolution: constructor > settings > DEFAULT ───────────────


@pytest.mark.asyncio
async def test_tool_agent_uses_settings_max_tool_steps() -> None:
    llm = FakeLLM(reply=_tool_reply())  # keeps requesting tools forever
    reg = _tool_registry()
    agent = ToolAgent(settings=AgentSettings(max_tool_steps=TEST_TOOL_MAX_STEPS))
    resp = await agent.handle(_req(("user", "go")), _ctx(llm=llm, tools=reg))
    assert len(llm.calls) == TEST_TOOL_MAX_STEPS
    assert resp.metadata["tool_steps"] == TEST_TOOL_MAX_STEPS


@pytest.mark.asyncio
async def test_tool_agent_constructor_arg_overrides_settings() -> None:
    llm = FakeLLM(reply=_tool_reply())
    reg = _tool_registry()
    agent = ToolAgent(
        max_tool_steps=TEST_TOOL_MAX_STEPS_OVERRIDE,
        settings=AgentSettings(max_tool_steps=TEST_TOOL_MAX_STEPS),
    )
    resp = await agent.handle(_req(("user", "go")), _ctx(llm=llm, tools=reg))
    assert len(llm.calls) == TEST_TOOL_MAX_STEPS_OVERRIDE
    assert resp.metadata["tool_steps"] == TEST_TOOL_MAX_STEPS_OVERRIDE


@pytest.mark.asyncio
@pytest.mark.parametrize("settings", [None, AgentSettings()])
async def test_tool_agent_defaults_to_config_constant(settings: AgentSettings | None) -> None:
    llm = FakeLLM(reply=_tool_reply())
    reg = _tool_registry()
    agent = ToolAgent(settings=settings)
    resp = await agent.handle(_req(("user", "go")), _ctx(llm=llm, tools=reg))
    assert len(llm.calls) == DEFAULT_TOOL_MAX_STEPS
    assert resp.metadata["tool_steps"] == DEFAULT_TOOL_MAX_STEPS


def test_agent_settings_max_tool_steps_env_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOOL_MAX_STEPS_ENV, str(TEST_TOOL_MAX_STEPS))
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.agents["tool"].max_tool_steps == TEST_TOOL_MAX_STEPS


def test_agent_settings_max_tool_steps_defaults_to_none() -> None:
    assert AgentSettings().max_tool_steps is None
