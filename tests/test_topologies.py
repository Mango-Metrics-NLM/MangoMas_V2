"""Tests for multi-agent topology methods: dispatch_pipeline and dispatch_fan_out."""

from __future__ import annotations

import pytest

from mangomas.agents import ChatAgent
from mangomas.core import AgentContext, AgentRequest, AgentResponse, Message, Orchestrator
from mangomas.errors import AgentNotFound
from tests.fakes import FakeLLM


def _make_orch(llm: FakeLLM | None = None) -> Orchestrator:
    ctx = AgentContext(llm=llm or FakeLLM(), repo=None)
    orch = Orchestrator(ctx)
    orch.register(ChatAgent())
    return orch


def _req(content: str = "go") -> AgentRequest:
    return AgentRequest(messages=[Message(role="user", content=content)])


# ── dispatch_pipeline ─────────────────────────────────────────────────────────


async def test_pipeline_single_agent_passes_through() -> None:
    orch = _make_orch(FakeLLM(reply="hello"))
    resp = await orch.dispatch_pipeline(["chat"], _req())
    assert resp.content == "hello"


async def test_pipeline_threads_output_as_input() -> None:
    llm = FakeLLM(replies=["step-one", "step-two"])
    orch = _make_orch(llm)

    # Register a second chat instance under a different name.
    class ChatAlias(ChatAgent):
        name = "chat2"

    orch.register(ChatAlias())

    resp = await orch.dispatch_pipeline(["chat", "chat2"], _req("start"))
    # Second agent receives "step-one" as the user message.
    second_call = llm.calls[1]
    assert second_call[0].content == "step-one"
    assert resp.content == "step-two"


async def test_pipeline_empty_list_raises_value_error() -> None:
    orch = _make_orch()
    with pytest.raises(ValueError, match="non-empty"):
        await orch.dispatch_pipeline([], _req())


async def test_pipeline_unknown_agent_raises_agent_not_found() -> None:
    orch = _make_orch()
    with pytest.raises(AgentNotFound):
        await orch.dispatch_pipeline(["chat", "ghost"], _req())


async def test_pipeline_preserves_metadata_in_intermediate_request() -> None:
    llm = FakeLLM(replies=["out1", "out2"])
    orch = _make_orch(llm)

    class ChatAlias(ChatAgent):
        name = "chat2"

    orch.register(ChatAlias())
    resp = await orch.dispatch_pipeline(["chat", "chat2"], _req())
    # Final response comes from chat2.
    assert resp.agent == "chat2"


# ── dispatch_fan_out ──────────────────────────────────────────────────────────


async def test_fan_out_single_agent_returns_list_of_one() -> None:
    orch = _make_orch(FakeLLM(reply="one"))
    results = await orch.dispatch_fan_out(["chat"], _req())
    assert len(results) == 1
    assert results[0].content == "one"


async def test_fan_out_parallel_same_agent_both_receive_same_request() -> None:
    llm = FakeLLM(reply="parallel")
    orch = _make_orch(llm)

    class ChatAlias(ChatAgent):
        name = "chat2"

    orch.register(ChatAlias())
    results = await orch.dispatch_fan_out(["chat", "chat2"], _req("query"))
    assert len(results) == 2
    for r in results:
        assert r.content == "parallel"


async def test_fan_out_empty_list_raises_value_error() -> None:
    orch = _make_orch()
    with pytest.raises(ValueError, match="non-empty"):
        await orch.dispatch_fan_out([], _req())


async def test_fan_out_unknown_agent_propagates_immediately() -> None:
    orch = _make_orch()
    with pytest.raises(AgentNotFound):
        await orch.dispatch_fan_out(["chat", "ghost"], _req())


async def test_fan_out_results_order_matches_agent_names() -> None:
    """Results list is in the same order as agent_names."""
    llm = FakeLLM()
    ctx = AgentContext(llm=llm, repo=None)
    orch = Orchestrator(ctx)

    # Register agents that produce distinct outputs by patching reply per call.
    replies = {"a1": "alpha", "a2": "beta"}

    class NamedChat(ChatAgent):
        def __init__(self, agent_name: str, agent_reply: str) -> None:
            super().__init__()
            self.name = agent_name
            self._reply = agent_reply

        async def handle(self, _request: AgentRequest, _ctx: AgentContext) -> AgentResponse:
            return AgentResponse(content=self._reply, agent=self.name)

    for aname, areply in replies.items():
        orch.register(NamedChat(aname, areply))

    results = await orch.dispatch_fan_out(["a1", "a2"], _req())
    assert results[0].content == "alpha"
    assert results[1].content == "beta"
