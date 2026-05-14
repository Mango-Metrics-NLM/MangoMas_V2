"""Tests for ToolAgent: tool dispatch, error mapping, and loop behaviour."""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

import pytest

from mangomas.agents.tool_agent import ToolAgent
from mangomas.core.agent import AgentContext, AgentRequest, Message
from mangomas.core.tools import ToolRegistry
from mangomas.errors import ToolExecutionError, ToolNotFound
from mangomas.registry import Registry
from tests.constants import DEFAULT_TOOL_NAME
from tests.fakes import FakeLLM, FakeTool

MessageRole = Literal["system", "user", "assistant", "tool"]


def _tool_registry(tool: FakeTool | None = None) -> ToolRegistry:
    reg: ToolRegistry = Registry("tool")
    t = tool or FakeTool()
    reg.register(t.name, t)
    return reg


def _ctx(
    llm: FakeLLM | None = None,
    tools: ToolRegistry | None = None,
) -> AgentContext:
    return AgentContext(llm=llm or FakeLLM(), repo=None, tools=tools)


def _req(*msgs: tuple[MessageRole, str]) -> AgentRequest:
    return AgentRequest(messages=[Message(role=r, content=c) for r, c in msgs])


# ── Plain response (no tool call) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_agent_plain_response() -> None:
    llm = FakeLLM(reply="Hello!")
    agent = ToolAgent()
    resp = await agent.handle(_req(("user", "hi")), _ctx(llm=llm))
    assert resp.content == "Hello!"
    assert resp.agent == "tool"


# ── Single tool call then final response ──────────────────────────────────────


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_tool_agent_unknown_tool_raises_tool_not_found() -> None:
    tool_json = json.dumps({"tool": "ghost", "arguments": {}})
    llm = FakeLLM(reply=f"```json\n{tool_json}\n```")
    reg = _tool_registry()  # only has DEFAULT_TOOL_NAME
    agent = ToolAgent()
    with pytest.raises(ToolNotFound) as exc_info:
        await agent.handle(_req(("user", "go")), _ctx(llm=llm, tools=reg))
    assert exc_info.value.name == "ghost"


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


# ── max_tool_steps exhausted falls back to final LLM response ─────────────────


@pytest.mark.asyncio
async def test_tool_agent_exhausts_max_tool_steps() -> None:
    tool_json = json.dumps({"tool": DEFAULT_TOOL_NAME, "arguments": {}})
    tool_reply = f"```json\n{tool_json}\n```"
    llm = FakeLLM(replies=[tool_reply, tool_reply, "final"])
    tool = FakeTool()
    reg = _tool_registry(tool)
    agent = ToolAgent(max_tool_steps=2)
    resp = await agent.handle(_req(("user", "go")), _ctx(llm=llm, tools=reg))
    assert resp.content == "final"
    assert resp.metadata["tool_steps"] == 2


# ── No tools registered: no system prompt injection ───────────────────────────


@pytest.mark.asyncio
async def test_tool_agent_no_tools_no_tool_prompt() -> None:
    llm = FakeLLM(reply="ok")
    agent = ToolAgent()
    resp = await agent.handle(_req(("user", "hi")), _ctx(llm=llm, tools=None))
    assert resp.content == "ok"
    # Only the user message should be in the first call.
    assert llm.calls[0] == [Message(role="user", content="hi")]


# ── Custom system_prompt injected before user messages ────────────────────────


@pytest.mark.asyncio
async def test_tool_agent_custom_system_prompt_injected() -> None:
    llm = FakeLLM(reply="ok")
    agent = ToolAgent(system_prompt="Be concise.")
    await agent.handle(_req(("user", "hi")), _ctx(llm=llm))
    first_message = llm.calls[0][0]
    assert first_message.role == "system"
    assert first_message.content == "Be concise."


# ── System prompt not duplicated when already present ─────────────────────────


@pytest.mark.asyncio
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
