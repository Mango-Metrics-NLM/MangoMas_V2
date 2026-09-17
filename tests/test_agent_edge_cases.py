"""Tests for agent edge cases."""

from __future__ import annotations

from mangomas.agents import ChatAgent
from mangomas.core.agent import AgentContext, AgentRequest, Message
from tests.fakes import FakeLLM, FakeRepository


async def test_agent_handles_empty_response() -> None:
    llm = FakeLLM(reply="")
    agent = ChatAgent()
    ctx = AgentContext(llm=llm, repo=FakeRepository())
    req = AgentRequest(messages=[Message(role="user", content="hello")])
    
    resp = await agent.handle(req, ctx)
    assert resp.agent == "chat"
    assert resp.content == ""



async def test_agent_handles_whitespace_response() -> None:
    llm = FakeLLM(reply="   \n  \t  ")
    agent = ChatAgent()
    ctx = AgentContext(llm=llm, repo=FakeRepository())
    req = AgentRequest(messages=[Message(role="user", content="hello")])
    
    resp = await agent.handle(req, ctx)
    assert resp.agent == "chat"
    assert resp.content == "   \n  \t  "


