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

import logging

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
from mangomas.agents._prompt import (
    AGENT_LLM_OVERRIDES_EXTRAS_KEY,
    LLM_PROFILE_METADATA_KEY,
    LLM_PROFILES_EXTRAS_KEY,
    build_messages,
    resolve_llm,
    resolve_system_prompt,
    select_llm_profile,
)
from mangomas.config import AgentSettings
from mangomas.core.agent import AgentContext, AgentRequest, Message
from mangomas.core.tools import build_structured_prompt
from mangomas.errors import ConfigError
from tests.constants import (
    TEST_LLM_PROFILE_NAME,
    TEST_MAX_TOKENS_OVERRIDE,
    TEST_PROMPT_EXPLICIT,
    TEST_PROMPT_SETTINGS,
    TEST_PROMPT_SUFFIX,
    TEST_TEMPERATURE_OVERRIDE,
    UNKNOWN_LLM_PROFILE_NAME,
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


# ── resolve_llm: per-agent override resolution (ADR-0028) ─────────────────────


def test_resolve_llm_no_extras_returns_ctx_llm() -> None:
    default_llm = FakeLLM()
    ctx = AgentContext(llm=default_llm, repo=None)
    assert resolve_llm(ctx, "chat") is default_llm


def test_resolve_llm_override_present_for_agent_returns_override() -> None:
    default_llm = FakeLLM()
    override_llm = FakeLLM()
    ctx = AgentContext(
        llm=default_llm,
        repo=None,
        extras={"agent_llm_overrides": {"chat": override_llm}},
    )
    result = resolve_llm(ctx, "chat")
    assert result is override_llm
    assert result is not default_llm


def test_resolve_llm_override_present_for_other_agent_returns_ctx_llm() -> None:
    """Proves per-agent keying, not a global switch: an override configured for
    a *different* agent name must not leak into this agent's resolution."""
    default_llm = FakeLLM()
    override_llm = FakeLLM()
    ctx = AgentContext(
        llm=default_llm,
        repo=None,
        extras={"agent_llm_overrides": {"tool": override_llm}},
    )
    assert resolve_llm(ctx, "chat") is default_llm


# ── Per-agent LLM override: end-to-end identity (ADR-0028) ────────────────────
# Two distinct FakeLLM instances prove the *override* client (not the shared
# default) actually receives the request when configured for this agent's
# name, and that the shared default receives it (unchanged) when no override
# is configured — the backward-compatibility regression guard.


async def test_chat_agent_handle_uses_override_llm_when_configured() -> None:
    default_llm = FakeLLM()
    override_llm = FakeLLM()
    agent = ChatAgent()
    ctx = AgentContext(
        llm=default_llm,
        repo=None,
        extras={"agent_llm_overrides": {agent.name: override_llm}},
    )
    req = AgentRequest(messages=[Message(role="user", content="hi")])
    await agent.handle(req, ctx)
    assert override_llm.calls, "override client should have received the request"
    assert not default_llm.calls, "shared default client must not be called"


async def test_chat_agent_handle_uses_default_llm_when_no_override() -> None:
    default_llm = FakeLLM()
    agent = ChatAgent()
    ctx = AgentContext(llm=default_llm, repo=None)
    req = AgentRequest(messages=[Message(role="user", content="hi")])
    await agent.handle(req, ctx)
    assert default_llm.calls, "default client should receive the request when unconfigured"


async def test_chat_agent_stream_uses_override_llm_when_configured() -> None:
    default_llm = FakeLLM(chunks=["default"])
    override_llm = FakeLLM(chunks=["a", "b"])
    agent = ChatAgent()
    ctx = AgentContext(
        llm=default_llm,
        repo=None,
        extras={"agent_llm_overrides": {agent.name: override_llm}},
    )
    req = AgentRequest(messages=[Message(role="user", content="hi")])
    chunks = [c async for c in await agent.stream(req, ctx)]
    assert chunks == ["a", "b"]
    assert override_llm.calls
    assert not default_llm.calls


async def test_chat_agent_stream_uses_default_llm_when_no_override() -> None:
    default_llm = FakeLLM(chunks=["a", "b"])
    agent = ChatAgent()
    ctx = AgentContext(llm=default_llm, repo=None)
    req = AgentRequest(messages=[Message(role="user", content="hi")])
    chunks = [c async for c in await agent.stream(req, ctx)]
    assert chunks == ["a", "b"]
    assert default_llm.calls


async def test_tool_agent_handle_uses_override_llm_when_configured() -> None:
    default_llm = FakeLLM(reply="Plain response.")
    override_llm = FakeLLM(reply="Plain response.")
    agent = ToolAgent()
    ctx = AgentContext(
        llm=default_llm,
        repo=None,
        extras={"agent_llm_overrides": {agent.name: override_llm}},
    )
    req = AgentRequest(messages=[Message(role="user", content="go")])
    await agent.handle(req, ctx)
    assert override_llm.calls
    assert not default_llm.calls


async def test_tool_agent_handle_uses_default_llm_when_no_override() -> None:
    default_llm = FakeLLM(reply="Plain response.")
    agent = ToolAgent()
    ctx = AgentContext(llm=default_llm, repo=None)
    req = AgentRequest(messages=[Message(role="user", content="go")])
    await agent.handle(req, ctx)
    assert default_llm.calls


async def test_summarize_agent_handle_uses_override_llm_when_configured() -> None:
    default_llm = FakeLLM(reply="summary")
    override_llm = FakeLLM(reply="summary")
    agent = SummarizeAgent()
    ctx = AgentContext(
        llm=default_llm,
        repo=None,
        extras={"agent_llm_overrides": {agent.name: override_llm}},
    )
    req = AgentRequest(messages=[Message(role="user", content="summarize")])
    await agent.handle(req, ctx)
    assert override_llm.calls
    assert not default_llm.calls


async def test_summarize_agent_handle_uses_default_llm_when_no_override() -> None:
    default_llm = FakeLLM(reply="summary")
    agent = SummarizeAgent()
    ctx = AgentContext(llm=default_llm, repo=None)
    req = AgentRequest(messages=[Message(role="user", content="summarize")])
    await agent.handle(req, ctx)
    assert default_llm.calls


async def test_planner_agent_handle_uses_override_llm_when_configured() -> None:
    reply = '{"goal":"g","steps":[{"step":1,"description":"d"}]}'
    default_llm = FakeLLM(reply=reply)
    override_llm = FakeLLM(reply=reply)
    agent = PlannerAgent()
    ctx = AgentContext(
        llm=default_llm,
        repo=None,
        extras={"agent_llm_overrides": {agent.name: override_llm}},
    )
    req = AgentRequest(messages=[Message(role="user", content="plan")])
    await agent.handle(req, ctx)
    assert override_llm.calls
    assert not default_llm.calls


async def test_planner_agent_stream_uses_override_llm_when_configured() -> None:
    default_llm = FakeLLM(chunks=["default"])
    override_llm = FakeLLM(chunks=["a", "b"])
    agent = PlannerAgent()
    ctx = AgentContext(
        llm=default_llm,
        repo=None,
        extras={"agent_llm_overrides": {agent.name: override_llm}},
    )
    req = AgentRequest(messages=[Message(role="user", content="plan")])
    chunks = [c async for c in await agent.stream(req, ctx)]
    assert chunks == ["a", "b"]
    assert override_llm.calls
    assert not default_llm.calls


async def test_reviewer_agent_handle_uses_default_llm_when_no_override() -> None:
    """Inverse/back-compat case, exercised on the sibling structured agent so
    both PlannerAgent and ReviewerAgent get at least one direct assertion."""
    default_llm = FakeLLM(reply='{"passed":true,"score":1.0,"feedback":"ok"}')
    agent = ReviewerAgent()
    ctx = AgentContext(llm=default_llm, repo=None)
    req = AgentRequest(messages=[Message(role="user", content="review")])
    await agent.handle(req, ctx)
    assert default_llm.calls


# ── spec-0040: request-scoped profile selection ───────────────────────────────


_SHADOW_EVENT = "llm_profile_shadows_override"


def _shadow_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "event", None) == _SHADOW_EVENT]


def _profile_request(name: object | None) -> AgentRequest:
    metadata = {} if name is None else {LLM_PROFILE_METADATA_KEY: name}
    return AgentRequest(messages=[Message(role="user", content="hi")], metadata=metadata)


def test_select_llm_profile_absent_returns_none() -> None:
    assert select_llm_profile({}, {}) is None
    assert select_llm_profile({LLM_PROFILES_EXTRAS_KEY: {"fast": FakeLLM()}}, {}) is None


def test_select_llm_profile_known_returns_name() -> None:
    extras = {LLM_PROFILES_EXTRAS_KEY: {TEST_LLM_PROFILE_NAME: FakeLLM()}}
    metadata = {LLM_PROFILE_METADATA_KEY: TEST_LLM_PROFILE_NAME}
    assert select_llm_profile(extras, metadata) == TEST_LLM_PROFILE_NAME


def test_select_llm_profile_unknown_raises_config_error_naming_it() -> None:
    """An unknown name is a 400-class ``ConfigError``, and the message says which."""
    extras = {LLM_PROFILES_EXTRAS_KEY: {TEST_LLM_PROFILE_NAME: FakeLLM()}}
    with pytest.raises(ConfigError, match=UNKNOWN_LLM_PROFILE_NAME):
        select_llm_profile(extras, {LLM_PROFILE_METADATA_KEY: UNKNOWN_LLM_PROFILE_NAME})
    # No profiles configured at all: still a ConfigError, never a KeyError.
    with pytest.raises(ConfigError):
        select_llm_profile({}, {LLM_PROFILE_METADATA_KEY: TEST_LLM_PROFILE_NAME})


def test_select_llm_profile_non_string_raises() -> None:
    extras = {LLM_PROFILES_EXTRAS_KEY: {TEST_LLM_PROFILE_NAME: FakeLLM()}}
    with pytest.raises(ConfigError):
        select_llm_profile(extras, {LLM_PROFILE_METADATA_KEY: 7})


def test_resolve_llm_without_request_is_unchanged() -> None:
    """Default-OFF: the two-argument call keeps its spec-0028 behaviour exactly."""
    default_llm = FakeLLM()
    override_llm = FakeLLM()
    ctx = AgentContext(
        llm=default_llm,
        repo=None,
        extras={
            AGENT_LLM_OVERRIDES_EXTRAS_KEY: {"chat": override_llm},
            LLM_PROFILES_EXTRAS_KEY: {TEST_LLM_PROFILE_NAME: FakeLLM()},
        },
    )
    assert resolve_llm(ctx, "chat") is override_llm
    assert resolve_llm(ctx, "tool") is default_llm


def test_resolve_llm_request_without_profile_falls_through_to_override() -> None:
    default_llm = FakeLLM()
    override_llm = FakeLLM()
    ctx = AgentContext(
        llm=default_llm,
        repo=None,
        extras={
            AGENT_LLM_OVERRIDES_EXTRAS_KEY: {"chat": override_llm},
            LLM_PROFILES_EXTRAS_KEY: {TEST_LLM_PROFILE_NAME: FakeLLM()},
        },
    )
    assert resolve_llm(ctx, "chat", _profile_request(None)) is override_llm


def test_resolve_llm_profile_beats_agent_override() -> None:
    """R2 precedence: profile > MODEL_OVERRIDE > ctx.llm.

    Mutation proof: swap the two branches in ``resolve_llm`` and this goes red
    while ``test_resolve_llm_request_without_profile_falls_through_to_override``
    stays green — the pair discriminates the ordering, not just the lookup.
    """
    default_llm = FakeLLM()
    override_llm = FakeLLM()
    profile_llm = FakeLLM()
    ctx = AgentContext(
        llm=default_llm,
        repo=None,
        extras={
            AGENT_LLM_OVERRIDES_EXTRAS_KEY: {"chat": override_llm},
            LLM_PROFILES_EXTRAS_KEY: {TEST_LLM_PROFILE_NAME: profile_llm},
        },
    )
    result = resolve_llm(ctx, "chat", _profile_request(TEST_LLM_PROFILE_NAME))
    assert result is profile_llm
    assert result is not override_llm
    assert result is not default_llm


def test_resolve_llm_profile_shadow_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """The shadowed override is logged with structured fields (risk register row 3)."""
    ctx = AgentContext(
        llm=FakeLLM(),
        repo=None,
        extras={
            AGENT_LLM_OVERRIDES_EXTRAS_KEY: {"chat": FakeLLM()},
            LLM_PROFILES_EXTRAS_KEY: {TEST_LLM_PROFILE_NAME: FakeLLM()},
        },
    )
    with caplog.at_level("INFO", logger="mangomas.agents._prompt"):
        resolve_llm(ctx, "chat", _profile_request(TEST_LLM_PROFILE_NAME))
    shadow = [
        r for r in caplog.records if getattr(r, "event", None) == "llm_profile_shadows_override"
    ]
    assert len(shadow) == 1
    assert shadow[0].agent == "chat"  # type: ignore[attr-defined]
    assert shadow[0].llm_profile == TEST_LLM_PROFILE_NAME  # type: ignore[attr-defined]


def test_resolve_llm_profile_without_override_logs_no_shadow(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Negative half: no override, no shadow line — the log is a signal, not noise."""
    ctx = AgentContext(
        llm=FakeLLM(),
        repo=None,
        extras={LLM_PROFILES_EXTRAS_KEY: {TEST_LLM_PROFILE_NAME: FakeLLM()}},
    )
    with caplog.at_level("INFO", logger="mangomas.agents._prompt"):
        resolve_llm(ctx, "chat", _profile_request(TEST_LLM_PROFILE_NAME))
    assert not [
        r for r in caplog.records if getattr(r, "event", None) == "llm_profile_shadows_override"
    ]


def test_resolve_llm_unknown_profile_raises() -> None:
    ctx = AgentContext(llm=FakeLLM(), repo=None, extras={LLM_PROFILES_EXTRAS_KEY: {}})
    with pytest.raises(ConfigError, match=UNKNOWN_LLM_PROFILE_NAME):
        resolve_llm(ctx, "chat", _profile_request(UNKNOWN_LLM_PROFILE_NAME))


@pytest.mark.parametrize(
    "agent",
    [ChatAgent(), SummarizeAgent(), ToolAgent(), PlannerAgent(), ReviewerAgent()],
    ids=["chat", "summarize", "tool", "planner", "reviewer"],
)
async def test_every_builtin_agent_handle_uses_the_profile_client(agent: object) -> None:
    """Each built-in agent threads ``request`` into ``resolve_llm``.

    A caller that forgot the third argument would silently run on the shared
    client while the response claimed a profile — the exact defect R2 exists
    to prevent, and one this test sees per agent.
    """
    default_llm = FakeLLM(reply='{"steps": [], "ok": true, "issues": [], "score": 1}')
    profile_llm = FakeLLM(reply='{"steps": [], "ok": true, "issues": [], "score": 1}')
    ctx = AgentContext(
        llm=default_llm,
        repo=None,
        extras={LLM_PROFILES_EXTRAS_KEY: {TEST_LLM_PROFILE_NAME: profile_llm}},
    )
    await agent.handle(_profile_request(TEST_LLM_PROFILE_NAME), ctx)  # type: ignore[attr-defined]
    assert profile_llm.calls, "profile client should have received the request"
    assert not default_llm.calls, "shared default client must not be called"


@pytest.mark.parametrize(
    "agent",
    [ChatAgent(), PlannerAgent(), ReviewerAgent()],
    ids=["chat", "planner", "reviewer"],
)
async def test_streaming_agents_stream_uses_the_profile_client(agent: object) -> None:
    default_llm = FakeLLM(chunks=["default"])
    profile_llm = FakeLLM(chunks=["profile"])
    ctx = AgentContext(
        llm=default_llm,
        repo=None,
        extras={LLM_PROFILES_EXTRAS_KEY: {TEST_LLM_PROFILE_NAME: profile_llm}},
    )
    stream = await agent.stream(_profile_request(TEST_LLM_PROFILE_NAME), ctx)  # type: ignore[attr-defined]
    received = [chunk async for chunk in stream]
    assert received == ["profile"]
    assert not default_llm.calls
