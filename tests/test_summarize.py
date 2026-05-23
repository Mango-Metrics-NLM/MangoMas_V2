"""Tests for SummarizeAgent."""

from __future__ import annotations

import pytest

from mangomas.agents.summarize import SummarizeAgent, _format_turns
from mangomas.config import AgentSettings
from mangomas.core import AgentContext, AgentRequest, AgentResponse, Message
from tests.fakes import FakeLLM, FakeRepository

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_request(text: str = "summarize please") -> AgentRequest:
    return AgentRequest(messages=[Message(role="user", content=text)])


async def _invoke(
    agent: SummarizeAgent,
    request: AgentRequest,
    llm: FakeLLM | None = None,
    repo: FakeRepository | None = None,
) -> AgentResponse:
    fake_llm = llm if llm is not None else FakeLLM()
    ctx = AgentContext(llm=fake_llm, repo=repo)
    return await agent.handle(request, ctx)


# ── Name / construction ───────────────────────────────────────────────────────


def test_summarize_agent_name() -> None:
    assert SummarizeAgent().name == "summarize"


def test_summarize_agent_default_system_prompt() -> None:
    agent = SummarizeAgent()
    # System prompt is not None and not empty
    assert agent._system_prompt


def test_summarize_agent_custom_system_prompt() -> None:
    agent = SummarizeAgent(system_prompt="Be brief.")
    assert agent._system_prompt == "Be brief."


def test_summarize_agent_settings_override() -> None:
    s = AgentSettings(system_prompt="From settings.")
    agent = SummarizeAgent(system_prompt="Original.", settings=s)
    assert agent._system_prompt == "From settings."


# ── _format_turns ─────────────────────────────────────────────────────────────


def test_format_turns_empty() -> None:
    assert _format_turns([]) == ""


def test_format_turns_renders_user_and_assistant() -> None:
    turns = [
        {
            "agent": "chat",
            "request": {"messages": [{"role": "user", "content": "hello"}]},
            "response": {"content": "hi there"},
        }
    ]
    text = _format_turns(turns)
    assert "[chat] User: hello" in text
    assert "[chat] Assistant: hi there" in text


def test_format_turns_chronological_order() -> None:
    """list_turns returns newest-first; _format_turns must reverse to oldest-first."""
    turns = [
        {
            "agent": "chat",
            "request": {"messages": [{"role": "user", "content": "second"}]},
            "response": {"content": "second-reply"},
        },
        {
            "agent": "chat",
            "request": {"messages": [{"role": "user", "content": "first"}]},
            "response": {"content": "first-reply"},
        },
    ]
    text = _format_turns(turns)
    assert text.index("first") < text.index("second")


def test_format_turns_missing_user_message() -> None:
    """Graceful when request has no user message: only the assistant line appears."""
    turns = [
        {
            "agent": "chat",
            "request": {"messages": []},
            "response": {"content": "response"},
        }
    ]
    text = _format_turns(turns)
    assert "[chat] User:" not in text  # nothing to render for empty message list
    assert "[chat] Assistant: response" in text


# ── handle() behaviour ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summarize_agent_with_history() -> None:
    repo = FakeRepository()
    llm = FakeLLM(reply="Summary of conversation.")

    # Pre-populate the repo with a turn
    request0 = AgentRequest(messages=[Message(role="user", content="Tell me a joke.")])
    response0 = AgentResponse(content="Why did the robot cross the road?", agent="chat")
    await repo.save_turn("chat", request0, response0)

    agent = SummarizeAgent()
    result = await _invoke(agent, _make_request(), llm=llm, repo=repo)

    assert result.content == "Summary of conversation."
    assert result.agent == "summarize"
    # LLM should have been called with messages including the history
    assert len(llm.calls) == 1
    combined = " ".join(m.content for m in llm.calls[0])
    assert "Tell me a joke" in combined


@pytest.mark.asyncio
async def test_summarize_agent_no_repo_graceful() -> None:
    """No repository → agent falls back gracefully, still calls LLM."""
    llm = FakeLLM(reply="Nothing to summarize.")
    agent = SummarizeAgent()
    result = await _invoke(agent, _make_request("summarize"), llm=llm, repo=None)
    assert result.content == "Nothing to summarize."
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_summarize_agent_empty_request_message() -> None:
    """Empty messages list → falls back to default user content."""
    llm = FakeLLM(reply="OK")
    agent = SummarizeAgent()
    request = AgentRequest(messages=[])
    ctx = AgentContext(llm=llm, repo=None)
    result = await agent.handle(request, ctx)
    assert result.content == "OK"


@pytest.mark.asyncio
async def test_summarize_agent_history_limit() -> None:
    """history_limit caps the number of turns fetched."""
    repo = FakeRepository()
    llm = FakeLLM(reply="summary")
    # Insert 5 turns
    for i in range(5):
        req = AgentRequest(messages=[Message(role="user", content=f"msg {i}")])
        resp = AgentResponse(content=f"reply {i}", agent="chat")
        await repo.save_turn("chat", req, resp)

    agent = SummarizeAgent(history_limit=2)
    await _invoke(agent, _make_request(), llm=llm, repo=repo)
    # list_turns(limit=2) only returns 2 turns → only 2 appear in the prompt
    combined = " ".join(m.content for m in llm.calls[0])
    assert combined.count("reply") == 2
