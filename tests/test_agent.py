"""Tests for the chat agent."""

from __future__ import annotations

from mangomas.adapters.storage import SQLiteRepository
from mangomas.agents import ChatAgent
from mangomas.core import AgentContext, AgentRequest, Message
from tests.fakes import FakeLLM


async def test_chat_agent_forwards_to_llm(fake_llm: FakeLLM, repo: SQLiteRepository) -> None:
    fake_llm.reply = "hi there"
    ctx = AgentContext(llm=fake_llm, repo=repo)
    agent = ChatAgent()

    req = AgentRequest(messages=[Message(role="user", content="hello")])
    resp = await agent.handle(req, ctx)

    assert resp.content == "hi there"
    assert resp.agent == "chat"
    assert fake_llm.calls[0][0].role == "user"


async def test_chat_agent_injects_system_prompt(fake_llm: FakeLLM, repo: SQLiteRepository) -> None:
    ctx = AgentContext(llm=fake_llm, repo=repo)
    agent = ChatAgent(system_prompt="you are mango")

    req = AgentRequest(messages=[Message(role="user", content="ping")])
    await agent.handle(req, ctx)

    sent = fake_llm.calls[0]
    assert sent[0].role == "system"
    assert sent[0].content == "you are mango"
    assert sent[1].role == "user"


async def test_chat_agent_does_not_duplicate_system(
    fake_llm: FakeLLM, repo: SQLiteRepository
) -> None:
    ctx = AgentContext(llm=fake_llm, repo=repo)
    agent = ChatAgent(system_prompt="default")

    req = AgentRequest(
        messages=[
            Message(role="system", content="explicit"),
            Message(role="user", content="ping"),
        ]
    )
    await agent.handle(req, ctx)

    sent = fake_llm.calls[0]
    assert [m.role for m in sent] == ["system", "user"]
    assert sent[0].content == "explicit"
