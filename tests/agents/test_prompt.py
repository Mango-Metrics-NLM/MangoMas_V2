"""Tests for the shared prompt-resolution helpers and live per-agent tuning.

Covers (spec-0014 milestone M5):

* ``resolve_system_prompt``'s precedence matrix — both the "settings-first"
  mode (``ChatAgent``/``SummarizeAgent``/``ToolAgent``) and the
  "explicit-first" mode (``PlannerAgent``/``ReviewerAgent`` via
  ``StructuredOutputAgent``) — with and without a suffix — and confirmation
  that each real agent constructor actually invokes the helper in the mode
  matching its historical behaviour.
* ``build_messages``'s prepend-unless-present idiom.
* Byte-identical planner/reviewer system-message content across the
  ``StructuredOutputAgent`` extraction (compared against the independently
  computed ``build_structured_prompt`` oracle, not a re-implementation).
* ``AgentSettings.temperature`` / ``.max_tokens`` actually flowing from
  settings through to the LLM client's ``complete()``/``stream()`` call for
  chat, planner, summarize, and tool_agent.
* ``LLMClient``/``StreamingLLMClient`` protocol conformance after adding the
  ``max_tokens`` keyword to ``FakeLLM``.
"""

from __future__ import annotations

import pytest

from mangomas.adapters.llm.base import LLMClient, PingableLLMClient, StreamingLLMClient
from mangomas.agents import (
    ChatAgent,
    ExecutionPlan,
    PlannerAgent,
    ReviewerAgent,
    ReviewResult,
    SummarizeAgent,
    ToolAgent,
)
from mangomas.agents._prompt import build_messages, resolve_system_prompt
from mangomas.config import AgentSettings
from mangomas.core.agent import AgentContext, AgentRequest, Message
from mangomas.core.tools import build_structured_prompt
from tests.constants import (
    TEST_MAX_TOKENS_OVERRIDE,
    TEST_PROMPT_EXPLICIT,
    TEST_PROMPT_SETTINGS,
    TEST_PROMPT_SUFFIX,
    TEST_TEMPERATURE_OVERRIDE,
)
from tests.fakes import FakeLLM

# ── resolve_system_prompt: precedence matrix (no suffix) ──────────────────────


@pytest.mark.parametrize(
    ("explicit", "settings_prompt", "expected"),
    [
        (None, None, None),
        (TEST_PROMPT_EXPLICIT, None, TEST_PROMPT_EXPLICIT),
        (None, TEST_PROMPT_SETTINGS, TEST_PROMPT_SETTINGS),
        (TEST_PROMPT_EXPLICIT, TEST_PROMPT_SETTINGS, TEST_PROMPT_SETTINGS),
    ],
    ids=["neither", "explicit-only", "settings-only", "both-settings-wins"],
)
def test_resolve_system_prompt_settings_first_default(
    explicit: str | None, settings_prompt: str | None, expected: str | None
) -> None:
    """Default mode (prefer_explicit=False): settings wins when set.

    This is the resolution rule actually used by ChatAgent, SummarizeAgent,
    and ToolAgent.
    """
    settings = AgentSettings(system_prompt=settings_prompt) if settings_prompt is not None else None
    assert resolve_system_prompt(explicit, settings) == expected


def test_resolve_system_prompt_settings_first_settings_object_present_but_prompt_none() -> None:
    """A settings object with system_prompt=None must not shadow explicit."""
    settings = AgentSettings()
    assert resolve_system_prompt(TEST_PROMPT_EXPLICIT, settings) == TEST_PROMPT_EXPLICIT
    assert resolve_system_prompt(None, settings) is None


@pytest.mark.parametrize(
    ("explicit", "settings_prompt", "expected"),
    [
        (None, None, None),
        (TEST_PROMPT_EXPLICIT, None, TEST_PROMPT_EXPLICIT),
        (None, TEST_PROMPT_SETTINGS, TEST_PROMPT_SETTINGS),
        (TEST_PROMPT_EXPLICIT, TEST_PROMPT_SETTINGS, TEST_PROMPT_EXPLICIT),
    ],
    ids=["neither", "explicit-only", "settings-only", "both-explicit-wins"],
)
def test_resolve_system_prompt_explicit_first(
    explicit: str | None, settings_prompt: str | None, expected: str | None
) -> None:
    """prefer_explicit=True: explicit wins when given.

    This is the resolution rule actually used by PlannerAgent/ReviewerAgent
    (via StructuredOutputAgent) — the opposite of the default mode above.
    """
    settings = AgentSettings(system_prompt=settings_prompt) if settings_prompt is not None else None
    assert resolve_system_prompt(explicit, settings, prefer_explicit=True) == expected


# ── resolve_system_prompt: suffix combination ─────────────────────────────────


def test_resolve_system_prompt_suffix_alone_when_no_base() -> None:
    assert resolve_system_prompt(None, None, suffix=TEST_PROMPT_SUFFIX) == TEST_PROMPT_SUFFIX


def test_resolve_system_prompt_suffix_concatenated_with_base() -> None:
    result = resolve_system_prompt(TEST_PROMPT_EXPLICIT, None, suffix=TEST_PROMPT_SUFFIX)
    assert result == f"{TEST_PROMPT_EXPLICIT}\n\n{TEST_PROMPT_SUFFIX}"


def test_resolve_system_prompt_suffix_with_settings_first_precedence() -> None:
    """Settings-first mode + suffix: settings wins the base, then suffix is appended.

    This is the shape ToolAgent's second (per-request) call reproduces —
    except ToolAgent passes settings=None on that call since the base is
    already resolved.
    """
    settings = AgentSettings(system_prompt=TEST_PROMPT_SETTINGS)
    result = resolve_system_prompt(TEST_PROMPT_EXPLICIT, settings, suffix=TEST_PROMPT_SUFFIX)
    assert result == f"{TEST_PROMPT_SETTINGS}\n\n{TEST_PROMPT_SUFFIX}"


def test_resolve_system_prompt_suffix_with_explicit_first_precedence() -> None:
    """Explicit-first mode + suffix: the PlannerAgent/ReviewerAgent shape."""
    settings = AgentSettings(system_prompt=TEST_PROMPT_SETTINGS)
    result = resolve_system_prompt(
        TEST_PROMPT_EXPLICIT, settings, suffix=TEST_PROMPT_SUFFIX, prefer_explicit=True
    )
    assert result == f"{TEST_PROMPT_EXPLICIT}\n\n{TEST_PROMPT_SUFFIX}"


def test_resolve_system_prompt_suffix_reused_for_tool_prompt_combine() -> None:
    """The exact call shape ToolAgent.handle() uses to fold in the tool prompt:
    explicit=<already-resolved self._system_prompt>, settings=None."""
    already_resolved = TEST_PROMPT_EXPLICIT
    result = resolve_system_prompt(already_resolved, None, suffix=TEST_PROMPT_SUFFIX)
    assert result == f"{already_resolved}\n\n{TEST_PROMPT_SUFFIX}"
    # No tools registered this request -> suffix is None -> base passes through.
    assert resolve_system_prompt(already_resolved, None, suffix=None) == already_resolved


# ── resolve_system_prompt: matches each of the five real agents' resolution ──


def test_chat_agent_uses_settings_first_precedence() -> None:
    settings = AgentSettings(system_prompt=TEST_PROMPT_SETTINGS)
    agent = ChatAgent(system_prompt=TEST_PROMPT_EXPLICIT, settings=settings)
    assert agent._system_prompt == resolve_system_prompt(TEST_PROMPT_EXPLICIT, settings)
    assert agent._system_prompt == TEST_PROMPT_SETTINGS


def test_chat_agent_falls_back_to_explicit_when_settings_prompt_absent() -> None:
    agent = ChatAgent(system_prompt=TEST_PROMPT_EXPLICIT, settings=AgentSettings())
    assert agent._system_prompt == TEST_PROMPT_EXPLICIT


def test_summarize_agent_uses_settings_first_precedence() -> None:
    settings = AgentSettings(system_prompt=TEST_PROMPT_SETTINGS)
    agent = SummarizeAgent(system_prompt=TEST_PROMPT_EXPLICIT, settings=settings)
    assert agent._system_prompt == resolve_system_prompt(TEST_PROMPT_EXPLICIT, settings)
    assert agent._system_prompt == TEST_PROMPT_SETTINGS


def test_tool_agent_uses_settings_first_precedence() -> None:
    settings = AgentSettings(system_prompt=TEST_PROMPT_SETTINGS)
    agent = ToolAgent(system_prompt=TEST_PROMPT_EXPLICIT, settings=settings)
    assert agent._system_prompt == resolve_system_prompt(TEST_PROMPT_EXPLICIT, settings)
    assert agent._system_prompt == TEST_PROMPT_SETTINGS


def test_planner_agent_uses_explicit_first_precedence() -> None:
    settings = AgentSettings(system_prompt=TEST_PROMPT_SETTINGS)
    agent = PlannerAgent(system_prompt=TEST_PROMPT_EXPLICIT, settings=settings)
    schema_prompt = build_structured_prompt(ExecutionPlan.model_json_schema())
    expected = resolve_system_prompt(
        TEST_PROMPT_EXPLICIT, settings, suffix=schema_prompt, prefer_explicit=True
    )
    assert agent._system_prompt == expected == f"{TEST_PROMPT_EXPLICIT}\n\n{schema_prompt}"


def test_planner_agent_falls_back_to_settings_when_explicit_absent() -> None:
    settings = AgentSettings(system_prompt=TEST_PROMPT_SETTINGS)
    agent = PlannerAgent(settings=settings)
    schema_prompt = build_structured_prompt(ExecutionPlan.model_json_schema())
    assert agent._system_prompt == f"{TEST_PROMPT_SETTINGS}\n\n{schema_prompt}"


def test_reviewer_agent_uses_explicit_first_precedence() -> None:
    settings = AgentSettings(system_prompt=TEST_PROMPT_SETTINGS)
    agent = ReviewerAgent(system_prompt=TEST_PROMPT_EXPLICIT, settings=settings)
    schema_prompt = build_structured_prompt(ReviewResult.model_json_schema())
    expected = resolve_system_prompt(
        TEST_PROMPT_EXPLICIT, settings, suffix=schema_prompt, prefer_explicit=True
    )
    assert agent._system_prompt == expected == f"{TEST_PROMPT_EXPLICIT}\n\n{schema_prompt}"


# ── build_messages ─────────────────────────────────────────────────────────────


def test_build_messages_no_system_prompt_no_insert() -> None:
    request = AgentRequest(messages=[Message(role="user", content="hi")])
    assert build_messages(request, None) == [Message(role="user", content="hi")]


def test_build_messages_empty_string_prompt_no_insert() -> None:
    request = AgentRequest(messages=[Message(role="user", content="hi")])
    assert build_messages(request, "") == [Message(role="user", content="hi")]


def test_build_messages_inserts_system_when_absent() -> None:
    request = AgentRequest(messages=[Message(role="user", content="hi")])
    result = build_messages(request, TEST_PROMPT_EXPLICIT)
    assert result[0] == Message(role="system", content=TEST_PROMPT_EXPLICIT)
    assert result[1] == Message(role="user", content="hi")


def test_build_messages_preserves_existing_system() -> None:
    request = AgentRequest(
        messages=[
            Message(role="system", content="existing"),
            Message(role="user", content="hi"),
        ]
    )
    result = build_messages(request, TEST_PROMPT_EXPLICIT)
    assert result == list(request.messages)


def test_build_messages_does_not_mutate_request() -> None:
    request = AgentRequest(messages=[Message(role="user", content="hi")])
    snapshot = list(request.messages)
    build_messages(request, TEST_PROMPT_EXPLICIT)
    assert request.messages == snapshot


# ── Byte-identical planner/reviewer system-message content ────────────────────
# Proves the StructuredOutputAgent extraction did not alter the exact prompt
# text sent to the LLM — compared against build_structured_prompt (the
# pre-existing, untouched oracle both agents always built their prompt from).


def test_planner_default_system_prompt_is_byte_identical_to_schema_prompt() -> None:
    expected = build_structured_prompt(ExecutionPlan.model_json_schema())
    assert PlannerAgent()._system_prompt == expected


def test_reviewer_default_system_prompt_is_byte_identical_to_schema_prompt() -> None:
    expected = build_structured_prompt(ReviewResult.model_json_schema())
    assert ReviewerAgent()._system_prompt == expected


def test_planner_custom_prompt_system_message_is_byte_identical() -> None:
    schema_prompt = build_structured_prompt(ExecutionPlan.model_json_schema())
    expected = f"{TEST_PROMPT_EXPLICIT}\n\n{schema_prompt}"
    assert PlannerAgent(system_prompt=TEST_PROMPT_EXPLICIT)._system_prompt == expected


def test_reviewer_custom_prompt_system_message_is_byte_identical() -> None:
    schema_prompt = build_structured_prompt(ReviewResult.model_json_schema())
    expected = f"{TEST_PROMPT_EXPLICIT}\n\n{schema_prompt}"
    assert ReviewerAgent(system_prompt=TEST_PROMPT_EXPLICIT)._system_prompt == expected


async def test_planner_sent_system_message_is_byte_identical_end_to_end() -> None:
    """End-to-end: the actual Message object sent to the LLM, not just the attribute."""
    expected = build_structured_prompt(ExecutionPlan.model_json_schema())
    llm = FakeLLM(reply='{"goal":"g","steps":[{"step":1,"description":"d"}]}')
    ctx = AgentContext(llm=llm, repo=None)
    agent = PlannerAgent()
    req = AgentRequest(messages=[Message(role="user", content="go")])
    await agent.handle(req, ctx)
    assert llm.calls[0][0] == Message(role="system", content=expected)


async def test_reviewer_sent_system_message_is_byte_identical_end_to_end() -> None:
    expected = build_structured_prompt(ReviewResult.model_json_schema())
    llm = FakeLLM(reply='{"passed":true,"score":1.0,"feedback":"ok"}')
    ctx = AgentContext(llm=llm, repo=None)
    agent = ReviewerAgent()
    req = AgentRequest(messages=[Message(role="user", content="review")])
    await agent.handle(req, ctx)
    assert llm.calls[0][0] == Message(role="system", content=expected)


# ── Live temperature / max_tokens flow: AgentSettings -> LLM client call ──────


async def test_chat_agent_forwards_temperature_and_max_tokens() -> None:
    settings = AgentSettings(
        temperature=TEST_TEMPERATURE_OVERRIDE, max_tokens=TEST_MAX_TOKENS_OVERRIDE
    )
    llm = FakeLLM()
    ctx = AgentContext(llm=llm, repo=None)
    agent = ChatAgent(settings=settings)
    req = AgentRequest(messages=[Message(role="user", content="hi")])
    await agent.handle(req, ctx)
    assert llm.call_kwargs[-1] == {
        "temperature": TEST_TEMPERATURE_OVERRIDE,
        "max_tokens": TEST_MAX_TOKENS_OVERRIDE,
    }


async def test_chat_agent_without_settings_forwards_none() -> None:
    llm = FakeLLM()
    ctx = AgentContext(llm=llm, repo=None)
    agent = ChatAgent()
    req = AgentRequest(messages=[Message(role="user", content="hi")])
    await agent.handle(req, ctx)
    assert llm.call_kwargs[-1] == {"temperature": None, "max_tokens": None}


async def test_chat_agent_stream_forwards_temperature_and_max_tokens() -> None:
    settings = AgentSettings(
        temperature=TEST_TEMPERATURE_OVERRIDE, max_tokens=TEST_MAX_TOKENS_OVERRIDE
    )
    llm = FakeLLM(chunks=["a", "b"])
    ctx = AgentContext(llm=llm, repo=None)
    agent = ChatAgent(settings=settings)
    req = AgentRequest(messages=[Message(role="user", content="hi")])
    chunks = [c async for c in await agent.stream(req, ctx)]
    assert chunks == ["a", "b"]
    assert llm.call_kwargs[-1] == {
        "temperature": TEST_TEMPERATURE_OVERRIDE,
        "max_tokens": TEST_MAX_TOKENS_OVERRIDE,
    }


async def test_planner_agent_forwards_temperature_and_max_tokens() -> None:
    settings = AgentSettings(
        temperature=TEST_TEMPERATURE_OVERRIDE, max_tokens=TEST_MAX_TOKENS_OVERRIDE
    )
    llm = FakeLLM(reply='{"goal":"g","steps":[{"step":1,"description":"d"}]}')
    ctx = AgentContext(llm=llm, repo=None)
    agent = PlannerAgent(settings=settings)
    req = AgentRequest(messages=[Message(role="user", content="plan")])
    await agent.handle(req, ctx)
    assert llm.call_kwargs[-1] == {
        "temperature": TEST_TEMPERATURE_OVERRIDE,
        "max_tokens": TEST_MAX_TOKENS_OVERRIDE,
    }


async def test_planner_agent_stream_forwards_temperature_and_max_tokens() -> None:
    settings = AgentSettings(
        temperature=TEST_TEMPERATURE_OVERRIDE, max_tokens=TEST_MAX_TOKENS_OVERRIDE
    )
    llm = FakeLLM(chunks=["a"])
    ctx = AgentContext(llm=llm, repo=None)
    agent = PlannerAgent(settings=settings)
    req = AgentRequest(messages=[Message(role="user", content="plan")])
    chunks = [c async for c in await agent.stream(req, ctx)]
    assert chunks == ["a"]
    assert llm.call_kwargs[-1] == {
        "temperature": TEST_TEMPERATURE_OVERRIDE,
        "max_tokens": TEST_MAX_TOKENS_OVERRIDE,
    }


async def test_summarize_agent_forwards_temperature_and_max_tokens() -> None:
    settings = AgentSettings(
        temperature=TEST_TEMPERATURE_OVERRIDE, max_tokens=TEST_MAX_TOKENS_OVERRIDE
    )
    llm = FakeLLM(reply="summary")
    ctx = AgentContext(llm=llm, repo=None)
    agent = SummarizeAgent(settings=settings)
    req = AgentRequest(messages=[Message(role="user", content="summarize")])
    await agent.handle(req, ctx)
    assert llm.call_kwargs[-1] == {
        "temperature": TEST_TEMPERATURE_OVERRIDE,
        "max_tokens": TEST_MAX_TOKENS_OVERRIDE,
    }


async def test_tool_agent_forwards_temperature_and_max_tokens() -> None:
    settings = AgentSettings(
        temperature=TEST_TEMPERATURE_OVERRIDE, max_tokens=TEST_MAX_TOKENS_OVERRIDE
    )
    llm = FakeLLM(reply="Plain response.")
    ctx = AgentContext(llm=llm, repo=None)
    agent = ToolAgent(settings=settings)
    req = AgentRequest(messages=[Message(role="user", content="go")])
    await agent.handle(req, ctx)
    assert llm.call_kwargs[-1] == {
        "temperature": TEST_TEMPERATURE_OVERRIDE,
        "max_tokens": TEST_MAX_TOKENS_OVERRIDE,
    }


async def test_tool_agent_without_settings_forwards_none() -> None:
    llm = FakeLLM(reply="Plain response.")
    ctx = AgentContext(llm=llm, repo=None)
    agent = ToolAgent()
    req = AgentRequest(messages=[Message(role="user", content="go")])
    await agent.handle(req, ctx)
    assert llm.call_kwargs[-1] == {"temperature": None, "max_tokens": None}


# ── Protocol conformance after the FakeLLM signature change ───────────────────


def test_fake_llm_still_satisfies_llm_client_protocol() -> None:
    assert isinstance(FakeLLM(), LLMClient)


def test_fake_llm_still_satisfies_streaming_llm_client_protocol() -> None:
    assert isinstance(FakeLLM(), StreamingLLMClient)


def test_fake_llm_still_satisfies_pingable_llm_client_protocol() -> None:
    assert isinstance(FakeLLM(), PingableLLMClient)
