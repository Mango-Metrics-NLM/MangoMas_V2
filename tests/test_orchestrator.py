"""Tests for the orchestrator."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import cast

import pytest

from mangomas.agents import ChatAgent
from mangomas.core import AgentContext, AgentRequest, AgentResponse, Message, Orchestrator
from mangomas.errors import AgentNotFound
from tests.fakes import FakeLLM, FakeRepository


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


# ── stream_dispatch persistence (spec-0025 / ADR-0025) ────────────────────────


def _streaming_orchestrator(llm: FakeLLM, repo: FakeRepository | None) -> Orchestrator:
    ctx = AgentContext(llm=llm, repo=repo)
    orch = Orchestrator(ctx)
    orch.register(ChatAgent())
    return orch


async def test_stream_dispatch_full_drain_persists_turn() -> None:
    """A fully-drained stream is saved as one turn: joined content + stream metadata."""
    repo = FakeRepository()
    orch = _streaming_orchestrator(FakeLLM(chunks=["a", "b", "c"]), repo)
    req = AgentRequest(messages=[Message(role="user", content="x")])
    chunks = [chunk async for chunk in await orch.stream_dispatch("chat", req)]
    assert chunks == ["a", "b", "c"]
    rows = await repo.list_turns()
    assert len(rows) == 1
    assert rows[0]["agent"] == "chat"
    assert rows[0]["response"]["content"] == "abc"
    assert rows[0]["response"]["metadata"]["stream"] == {"chunks": 3, "degraded": False}


async def test_stream_dispatch_abandonment_does_not_persist() -> None:
    """Early consumer abandonment (aclose) never saves a half-drained turn."""
    repo = FakeRepository()
    orch = _streaming_orchestrator(FakeLLM(chunks=["a", "b", "c"]), repo)
    req = AgentRequest(messages=[Message(role="user", content="x")])
    stream = await orch.stream_dispatch("chat", req)
    first = await stream.__anext__()
    assert first == "a"
    # ``stream_dispatch`` is typed AsyncIterator[str]; the concrete value is
    # always an async generator, which is exactly what's under test here.
    await cast("AsyncGenerator[str, None]", stream).aclose()
    assert await repo.list_turns() == []


async def test_stream_dispatch_upstream_error_does_not_persist() -> None:
    """A mid-stream upstream failure propagates and persists nothing."""
    repo = FakeRepository()
    llm = FakeLLM(chunks=["a", "b"], raise_on_stream=RuntimeError("boom"), raise_after_chunks=1)
    orch = _streaming_orchestrator(llm, repo)
    req = AgentRequest(messages=[Message(role="user", content="x")])
    received: list[str] = []
    with pytest.raises(RuntimeError, match="boom"):
        async for chunk in await orch.stream_dispatch("chat", req):
            received.append(chunk)
    assert received == ["a"]
    assert await repo.list_turns() == []


async def test_stream_dispatch_degraded_fallback_warns_and_persists(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-StreamingAgent fallback: warning logged, one chunk, turn saved degraded=True."""
    repo = FakeRepository()
    ctx = AgentContext(llm=FakeLLM(reply="unused"), repo=repo)
    orch = Orchestrator(ctx)
    orch.register(_DummyAgent())
    req = AgentRequest(messages=[Message(role="user", content="x")])
    with caplog.at_level(logging.WARNING, logger="mangomas.core.orchestrator"):
        chunks = [chunk async for chunk in await orch.stream_dispatch("dummy", req)]
    assert chunks == ["got:1"]
    assert "'dummy'" in caplog.text
    assert "buffered into a single chunk" in caplog.text
    rows = await repo.list_turns()
    assert len(rows) == 1
    assert rows[0]["response"]["content"] == "got:1"
    assert rows[0]["response"]["metadata"]["stream"] == {"chunks": 1, "degraded": True}


async def test_stream_dispatch_without_repo_drains_without_persisting() -> None:
    """repo=None: full drain works and nothing is (or could be) persisted."""
    orch = _streaming_orchestrator(FakeLLM(chunks=["x", "y"]), None)
    req = AgentRequest(messages=[Message(role="user", content="x")])
    chunks = [chunk async for chunk in await orch.stream_dispatch("chat", req)]
    assert chunks == ["x", "y"]


# ── agent_supports_streaming ──────────────────────────────────────────────────


async def test_agent_supports_streaming_true_for_streaming_agent() -> None:
    orch = _streaming_orchestrator(FakeLLM(), None)
    assert orch.agent_supports_streaming("chat") is True


async def test_agent_supports_streaming_false_for_non_streaming_agent() -> None:
    ctx = AgentContext(llm=FakeLLM(), repo=None)
    orch = Orchestrator(ctx)
    orch.register(_DummyAgent())
    assert orch.agent_supports_streaming("dummy") is False


async def test_agent_supports_streaming_unknown_agent_raises() -> None:
    orch = _streaming_orchestrator(FakeLLM(), None)
    with pytest.raises(AgentNotFound):
        orch.agent_supports_streaming("nope")
