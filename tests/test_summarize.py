"""Tests for SummarizeAgent."""

from __future__ import annotations

import pytest

from mangomas.agents.summarize import SummarizeAgent, _format_turns
from mangomas.config import AgentSettings, Settings
from mangomas.core import AgentContext, AgentRequest, AgentResponse, Message
from tests.constants import (
    DEFAULT_SUMMARIZE_HISTORY_LIMIT,
    SUMMARIZE_HISTORY_LIMIT_ENV,
    TEST_HISTORY_LIMIT,
    TEST_HISTORY_LIMIT_OVERRIDE,
    TEST_HISTORY_SEEDED_TURNS,
)
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


async def test_summarize_agent_no_repo_graceful() -> None:
    """No repository → agent falls back gracefully, still calls LLM."""
    llm = FakeLLM(reply="Nothing to summarize.")
    agent = SummarizeAgent()
    result = await _invoke(agent, _make_request("summarize"), llm=llm, repo=None)
    assert result.content == "Nothing to summarize."
    assert len(llm.calls) == 1


async def test_summarize_agent_empty_request_message() -> None:
    """Empty messages list → falls back to default user content."""
    llm = FakeLLM(reply="OK")
    agent = SummarizeAgent()
    request = AgentRequest(messages=[])
    ctx = AgentContext(llm=llm, repo=None)
    result = await agent.handle(request, ctx)
    assert result.content == "OK"


async def _seeded_repo(turns: int = TEST_HISTORY_SEEDED_TURNS) -> FakeRepository:
    """Return a repository holding *turns* saved chat turns."""
    repo = FakeRepository()
    for i in range(turns):
        req = AgentRequest(messages=[Message(role="user", content=f"msg {i}")])
        resp = AgentResponse(content=f"reply {i}", agent="chat")
        await repo.save_turn("chat", req, resp)
    return repo


def _replies_in_prompt(llm: FakeLLM) -> int:
    """Count how many persisted turns actually reached the LLM prompt.

    ``list_turns(limit=n)`` slices, so this is a real observation of the
    resolved window rather than a restatement of the input.
    """
    return " ".join(m.content for m in llm.calls[0]).count("reply")


async def test_summarize_agent_history_limit() -> None:
    """history_limit caps the number of turns fetched."""
    repo = await _seeded_repo()
    llm = FakeLLM(reply="summary")

    agent = SummarizeAgent(history_limit=TEST_HISTORY_LIMIT)
    await _invoke(agent, _make_request(), llm=llm, repo=repo)
    assert _replies_in_prompt(llm) == TEST_HISTORY_LIMIT


# ── history_limit resolution: constructor > settings > DEFAULT ────────────────
# Mirrors the max_tool_steps block in tests/test_tool_agent.py — the same
# three-tier precedence, so the same five assertions apply.


async def test_summarize_agent_uses_settings_history_limit() -> None:
    repo = await _seeded_repo()
    llm = FakeLLM(reply="summary")
    agent = SummarizeAgent(settings=AgentSettings(history_limit=TEST_HISTORY_LIMIT))
    await _invoke(agent, _make_request(), llm=llm, repo=repo)
    assert _replies_in_prompt(llm) == TEST_HISTORY_LIMIT


async def test_summarize_agent_constructor_arg_overrides_settings() -> None:
    repo = await _seeded_repo()
    llm = FakeLLM(reply="summary")
    agent = SummarizeAgent(
        history_limit=TEST_HISTORY_LIMIT_OVERRIDE,
        settings=AgentSettings(history_limit=TEST_HISTORY_LIMIT),
    )
    await _invoke(agent, _make_request(), llm=llm, repo=repo)
    assert _replies_in_prompt(llm) == TEST_HISTORY_LIMIT_OVERRIDE


@pytest.mark.parametrize("settings", [None, AgentSettings()])
async def test_summarize_agent_defaults_to_config_constant(
    settings: AgentSettings | None,
) -> None:
    """Both halves of the ``settings is not None and ... is not None`` guard.

    ``None`` covers the missing-settings arm; ``AgentSettings()`` covers
    settings-present-but-field-unset, which a single ``None`` case would leave
    as a partial branch arc under ``branch = true``.
    """
    # Seed more turns than the default so a too-large window is observable.
    repo = await _seeded_repo(DEFAULT_SUMMARIZE_HISTORY_LIMIT + 2)
    llm = FakeLLM(reply="summary")
    agent = SummarizeAgent(settings=settings)
    await _invoke(agent, _make_request(), llm=llm, repo=repo)
    assert _replies_in_prompt(llm) == DEFAULT_SUMMARIZE_HISTORY_LIMIT


def test_agent_settings_history_limit_env_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The only assertion proving the value parses through ``dict[str, AgentSettings]``."""
    monkeypatch.setenv(SUMMARIZE_HISTORY_LIMIT_ENV, str(TEST_HISTORY_LIMIT))
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.agents["summarize"].history_limit == TEST_HISTORY_LIMIT


def test_agent_settings_history_limit_defaults_to_none() -> None:
    assert AgentSettings().history_limit is None
