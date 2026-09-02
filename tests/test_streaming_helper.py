"""Direct unit tests for :func:`mangomas.agents._streaming.stream_with_buffered_fallback`.

The per-agent tests in ``test_planner_stream.py`` and ``test_reviewer_stream.py``
already exercise this helper indirectly. This module covers the helper in
isolation so future agents that delegate to it get clear failure messages.
"""

from __future__ import annotations

import logging

import pytest

from mangomas.adapters.llm.base import LLMClient
from mangomas.agents._streaming import (
    _FALLBACK_WARNING_MESSAGE,
    stream_with_buffered_fallback,
)
from mangomas.core.agent import Message
from tests.fakes import FakeLLM, NonPingableFakeLLM

_AGENT_NAME = "streaming-helper-test"


async def _drain(messages: list[Message], llm: LLMClient) -> list[str]:
    chunks: list[str] = []
    async for token in stream_with_buffered_fallback(_AGENT_NAME, messages, llm):
        chunks.append(token)
    return chunks


async def test_yields_tokens_from_streaming_llm() -> None:
    llm = FakeLLM(chunks=["one", "two", "three"])
    messages = [Message(role="user", content="hi")]

    chunks = await _drain(messages, llm)

    assert chunks == ["one", "two", "three"]


async def test_falls_back_to_complete_when_llm_not_streaming(
    caplog: pytest.LogCaptureFixture,
) -> None:
    llm = NonPingableFakeLLM(reply="buffered reply")
    messages = [Message(role="user", content="hi")]

    with caplog.at_level(logging.WARNING, logger="mangomas.agents._streaming"):
        chunks = await _drain(messages, llm)

    assert chunks == ["buffered reply"]
    # The single-source-of-truth warning text must be emitted.
    fallback_records = [r for r in caplog.records if _FALLBACK_WARNING_MESSAGE in r.message]
    assert fallback_records, "expected the shared fallback warning"
    # The agent name must be propagated via extra={...} for log filtering.
    assert fallback_records[0].agent == _AGENT_NAME  # type: ignore[attr-defined]


async def test_does_not_modify_caller_message_list() -> None:
    """The helper must not append/insert into the caller's list."""
    llm = FakeLLM(chunks=["ok"])
    messages = [Message(role="user", content="immutable")]
    snapshot = list(messages)

    await _drain(messages, llm)

    assert messages == snapshot, "helper must not mutate the caller's message list"


async def test_streaming_path_does_not_emit_fallback_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    llm = FakeLLM(chunks=["tok"])
    messages = [Message(role="user", content="hi")]

    with caplog.at_level(logging.WARNING, logger="mangomas.agents._streaming"):
        await _drain(messages, llm)

    fallback_records = [r for r in caplog.records if _FALLBACK_WARNING_MESSAGE in r.message]
    assert not fallback_records, "streaming path must not log the fallback warning"
