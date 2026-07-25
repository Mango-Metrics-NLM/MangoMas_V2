"""Tests for the orchestrator."""

from __future__ import annotations

import logging

import pytest

from mangomas.agents import ChatAgent
from mangomas.core import AgentContext, AgentRequest, AgentResponse, Message, Orchestrator
from mangomas.errors import AgentNotFound
from tests.fakes import FakeLLM


class _DummyAgent:
    name = "dummy"

    async def handle(self, request: AgentRequest, _ctx: AgentContext) -> AgentResponse:
        return AgentResponse(content=f"got:{len(request.messages)}", agent=self.name)


async def test_dispatch_routes_to_registered_agent(orchestrator: Orchestrator) -> None:
    req = AgentRequest(messages=[Message(role="user", content="x")])
    resp = await orchestrator.dispatch("chat", req)
    assert resp.agent == "chat"


async def test_dispatch_unknown_agent_raises_agent_not_found(orchestrator: Orchestrator) -> None:
    with pytest.raises(AgentNotFound):
        await orchestrator.dispatch("nope", AgentRequest(messages=[]))


async def test_dispatch_unknown_agent_logs_error(
    orchestrator: Orchestrator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with (
        caplog.at_level(logging.ERROR, logger="mangomas.core.orchestrator"),
        pytest.raises(AgentNotFound),
    ):
        await orchestrator.dispatch("nope", AgentRequest(messages=[]))
    assert "agent not found" in caplog.text


async def test_dispatch_unknown_agent_caught_by_key_error(orchestrator: Orchestrator) -> None:
    """Back-compat: existing ``except KeyError`` callers still catch AgentNotFound."""
    with pytest.raises(KeyError):
        await orchestrator.dispatch("nope", AgentRequest(messages=[]))


async def test_register_rejects_blank_name(orchestrator: Orchestrator) -> None:
    class Bad:
        name = ""

        async def handle(self, _request: AgentRequest, _ctx: AgentContext) -> AgentResponse:
            return AgentResponse(content="", agent="")

    with pytest.raises(ValueError, match="non-empty"):
        orchestrator.register(Bad())


async def test_register_and_list(orchestrator: Orchestrator) -> None:
    orchestrator.register(_DummyAgent())
    assert "dummy" in orchestrator.list_agents()
    assert "chat" in orchestrator.list_agents()


async def test_dispatch_persists_turn(orchestrator: Orchestrator) -> None:
    req = AgentRequest(messages=[Message(role="user", content="x")])
    await orchestrator.dispatch("chat", req)
    repo = orchestrator.context.repo
    assert repo is not None
    rows = await repo.list_turns()
    assert len(rows) == 1
    assert rows[0]["agent"] == "chat"


async def test_dispatch_without_repo() -> None:
    ctx = AgentContext(llm=FakeLLM(reply="ok"), repo=None)
    orch = Orchestrator(ctx)
    orch.register(ChatAgent())
    resp = await orch.dispatch("chat", AgentRequest(messages=[Message(role="user", content="x")]))
    assert resp.content == "ok"


async def test_context_property_returns_context() -> None:
    """The public ``context`` property exposes the AgentContext."""
    ctx = AgentContext(llm=FakeLLM(reply="ok"), repo=None)
    orch = Orchestrator(ctx)
    assert orch.context is ctx


# ── dispatch max_steps validation ────────────────────────────────────────────


async def test_dispatch_max_steps_zero_raises_value_error(orchestrator: Orchestrator) -> None:
    req = AgentRequest(messages=[Message(role="user", content="x")])
    with pytest.raises(ValueError, match="max_steps must be >= 1"):
        await orchestrator.dispatch("chat", req, max_steps=0)


# ── dispatch_pipeline ─────────────────────────────────────────────────────────


async def test_dispatch_pipeline_empty_raises_value_error() -> None:
    ctx = AgentContext(llm=FakeLLM(reply="ok"), repo=None)
    orch = Orchestrator(ctx)
    with pytest.raises(ValueError, match="non-empty"):
        await orch.dispatch_pipeline([], AgentRequest(messages=[Message(role="user", content="x")]))


async def test_dispatch_pipeline_single_agent() -> None:
    ctx = AgentContext(llm=FakeLLM(reply="pipeline-reply"), repo=None)
    orch = Orchestrator(ctx)
    orch.register(ChatAgent())
    req = AgentRequest(messages=[Message(role="user", content="hello")])
    resp = await orch.dispatch_pipeline(["chat"], req)
    assert resp.content == "pipeline-reply"


async def test_dispatch_pipeline_two_agents() -> None:
    ctx = AgentContext(llm=FakeLLM(reply="chained"), repo=None)
    orch = Orchestrator(ctx)
    orch.register(ChatAgent())
    req = AgentRequest(messages=[Message(role="user", content="start")])
    resp = await orch.dispatch_pipeline(["chat", "chat"], req)
    assert resp.content == "chained"


# ── dispatch_fan_out ──────────────────────────────────────────────────────────


async def test_dispatch_fan_out_empty_raises_value_error() -> None:
    ctx = AgentContext(llm=FakeLLM(reply="ok"), repo=None)
    orch = Orchestrator(ctx)
    with pytest.raises(ValueError, match="non-empty"):
        await orch.dispatch_fan_out([], AgentRequest(messages=[Message(role="user", content="x")]))


async def test_dispatch_fan_out_parallel() -> None:
    ctx = AgentContext(llm=FakeLLM(reply="fan"), repo=None)
    orch = Orchestrator(ctx)
    orch.register(ChatAgent())
    req = AgentRequest(messages=[Message(role="user", content="go")])
    results = await orch.dispatch_fan_out(["chat", "chat"], req)
    assert len(results) == 2
    assert all(r.content == "fan" for r in results)


# ── stream_dispatch ───────────────────────────────────────────────────────────


async def test_stream_dispatch_unknown_agent_raises() -> None:
    ctx = AgentContext(llm=FakeLLM(reply="ok"), repo=None)
    orch = Orchestrator(ctx)
    with pytest.raises(AgentNotFound):
        await orch.stream_dispatch("nope", AgentRequest(messages=[]))


async def test_stream_dispatch_non_streaming_agent_yields_content() -> None:
    ctx = AgentContext(llm=FakeLLM(reply="streamed-as-non-stream"), repo=None)
    orch = Orchestrator(ctx)
    orch.register(_DummyAgent())
    req = AgentRequest(messages=[Message(role="user", content="x")])
    chunks: list[str] = []
    async for chunk in await orch.stream_dispatch("dummy", req):
        chunks.append(chunk)
    assert "".join(chunks) == "got:1"
