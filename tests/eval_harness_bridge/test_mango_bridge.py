"""Offline tests for the Agents->Mango-Mas bridge target.

No live server or model required: the HTTP call is mocked with respx, so the
schema bridge and prediction extraction are verified deterministically on every
CI run.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
import respx

import mango_bridge

_BASE_URL = "http://mango.test"


@pytest.fixture(autouse=True)
def _reset_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("MANGO_BASE_URL", _BASE_URL)
    monkeypatch.setenv("MANGO_AGENT", "chat")
    mango_bridge._client.cache_clear()
    yield
    mango_bridge._client.cache_clear()


def test_to_messages_passthrough_verbatim() -> None:
    turns = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "hi"},
    ]
    assert mango_bridge.to_messages({"messages": turns}) == turns


def test_to_messages_synthesizes_system_and_question() -> None:
    out = mango_bridge.to_messages({"system": "be terse", "question": "why?"})
    assert out == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "why?"},
    ]


def test_to_messages_prefers_question_then_prompt_then_input() -> None:
    assert mango_bridge.to_messages({"prompt": "P"}) == [{"role": "user", "content": "P"}]
    assert mango_bridge.to_messages({"input": "I"}) == [{"role": "user", "content": "I"}]
    assert mango_bridge.to_messages({}) == [{"role": "user", "content": ""}]


def test_to_messages_rejects_malformed_message_entry() -> None:
    with pytest.raises(ValueError, match="malformed message"):
        mango_bridge.to_messages({"messages": [{"role": "user"}]})
    with pytest.raises(ValueError, match="malformed message"):
        mango_bridge.to_messages({"messages": ["not-a-dict"]})


@respx.mock
def test_predict_posts_expected_body_and_returns_content() -> None:
    route = respx.post(f"{_BASE_URL}/agents/chat/invoke").mock(
        return_value=httpx.Response(200, json={"content": "hello", "agent": "chat", "metadata": {}})
    )
    assert mango_bridge.predict({"question": "hi"}) == "hello"
    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["max_steps"] == 1


@respx.mock
def test_predict_routes_to_per_row_agent_override() -> None:
    route = respx.post(f"{_BASE_URL}/agents/summarize/invoke").mock(
        return_value=httpx.Response(200, json={"content": "ok", "agent": "summarize"})
    )
    assert mango_bridge.predict({"question": "hi", "agent": "summarize"}) == "ok"
    assert route.called


@respx.mock
def test_predict_raises_on_non_2xx() -> None:
    respx.post(f"{_BASE_URL}/agents/chat/invoke").mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        mango_bridge.predict({"question": "hi"})


@respx.mock
def test_predict_raises_on_200_missing_content() -> None:
    respx.post(f"{_BASE_URL}/agents/chat/invoke").mock(
        return_value=httpx.Response(200, json={"agent": "chat"})
    )
    with pytest.raises(ValueError, match="missing 'content'"):
        mango_bridge.predict({"question": "hi"})
