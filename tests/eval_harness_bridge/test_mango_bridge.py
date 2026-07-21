"""Offline tests for the Agents->Mango-Mas bridge target.

No live server or model required: the HTTP call is mocked with respx, so the
schema bridge, prediction extraction, error paths, and config resolution are
verified deterministically on every CI run.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
import respx

import mango_bridge
from tests.eval_harness_bridge.constants import (
    BRIDGE_BASE_URL,
    DEFAULT_AGENT,
    HTTP_OK,
    HTTP_SERVICE_UNAVAILABLE,
    SUMMARIZE_AGENT,
    invoke_url,
    mango_response,
)


@pytest.fixture(autouse=True)
def _reset_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("MANGO_BASE_URL", BRIDGE_BASE_URL)
    monkeypatch.setenv("MANGO_AGENT", DEFAULT_AGENT)
    mango_bridge.close_client()
    yield
    mango_bridge.close_client()


# --- to_messages: passthrough --------------------------------------------------


def test_to_messages_passthrough_verbatim() -> None:
    turns = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "hi"},
    ]
    assert mango_bridge.to_messages({"messages": turns}) == turns


def test_to_messages_rejects_malformed_message_entry() -> None:
    with pytest.raises(ValueError, match="malformed message"):
        mango_bridge.to_messages({"messages": [{"role": "user"}]})
    with pytest.raises(ValueError, match="malformed message"):
        mango_bridge.to_messages({"messages": ["not-a-dict"]})
    with pytest.raises(ValueError, match="malformed message"):
        mango_bridge.to_messages({"messages": [{"role": "user", "content": None}]})


def test_to_messages_rejects_empty_or_non_list_messages() -> None:
    with pytest.raises(ValueError, match="non-empty list"):
        mango_bridge.to_messages({"messages": []})
    with pytest.raises(ValueError, match="non-empty list"):
        mango_bridge.to_messages({"messages": "nope"})


# --- to_messages: synthesis + precedence --------------------------------------


def test_to_messages_synthesizes_system_and_question() -> None:
    out = mango_bridge.to_messages({"system": "be terse", "question": "why?"})
    assert out == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "why?"},
    ]


def test_to_messages_precedence_is_question_then_prompt_then_input() -> None:
    # Competing keys present: precedence must be honoured, not just single-key.
    assert mango_bridge.to_messages({"question": "Q", "prompt": "P", "input": "I"}) == [
        {"role": "user", "content": "Q"}
    ]
    assert mango_bridge.to_messages({"prompt": "P", "input": "I"}) == [
        {"role": "user", "content": "P"}
    ]
    assert mango_bridge.to_messages({"input": "I"}) == [{"role": "user", "content": "I"}]
    assert mango_bridge.to_messages({}) == [{"role": "user", "content": ""}]


def test_to_messages_selects_present_key_even_when_empty() -> None:
    # Presence, not truthiness: an explicit empty question is still selected.
    assert mango_bridge.to_messages({"question": "", "prompt": "P"}) == [
        {"role": "user", "content": ""}
    ]


# --- predict -------------------------------------------------------------------


@respx.mock
def test_predict_posts_expected_body_and_returns_content() -> None:
    route = respx.post(invoke_url()).mock(
        return_value=httpx.Response(HTTP_OK, json=mango_response())
    )
    assert mango_bridge.predict({"question": "hi"}) == "hello"
    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["max_steps"] == 1
    assert body["metadata"] == {}


@respx.mock
def test_predict_honours_per_row_max_steps_and_metadata() -> None:
    route = respx.post(invoke_url()).mock(
        return_value=httpx.Response(HTTP_OK, json=mango_response())
    )
    mango_bridge.predict({"question": "hi", "max_steps": 3, "metadata": {"k": "v"}})
    body = json.loads(route.calls.last.request.content)
    assert body["max_steps"] == 3
    assert body["metadata"] == {"k": "v"}


def test_predict_rejects_non_mapping_metadata() -> None:
    with pytest.raises(ValueError, match="metadata must be an object"):
        mango_bridge.predict({"question": "hi", "metadata": ["not", "a", "map"]})


@respx.mock
def test_predict_routes_to_per_row_agent_override() -> None:
    route = respx.post(invoke_url(SUMMARIZE_AGENT)).mock(
        return_value=httpx.Response(HTTP_OK, json=mango_response("ok", agent=SUMMARIZE_AGENT))
    )
    assert mango_bridge.predict({"question": "hi", "agent": SUMMARIZE_AGENT}) == "ok"
    assert route.called


@respx.mock
def test_predict_raises_on_non_2xx() -> None:
    respx.post(invoke_url()).mock(return_value=httpx.Response(HTTP_SERVICE_UNAVAILABLE))
    with pytest.raises(httpx.HTTPStatusError):
        mango_bridge.predict({"question": "hi"})


@respx.mock
def test_predict_raises_on_200_missing_content() -> None:
    respx.post(invoke_url()).mock(
        return_value=httpx.Response(HTTP_OK, json={"agent": DEFAULT_AGENT})
    )
    with pytest.raises(ValueError, match="no string 'content'"):
        mango_bridge.predict({"question": "hi"})


@respx.mock
def test_predict_raises_on_non_dict_body() -> None:
    respx.post(invoke_url()).mock(return_value=httpx.Response(HTTP_OK, json=["not", "a", "dict"]))
    with pytest.raises(ValueError, match="no string 'content'"):
        mango_bridge.predict({"question": "hi"})


# --- client lifecycle & config resolution -------------------------------------


def test_client_reads_timeout_and_base_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGO_TIMEOUT", "5")
    mango_bridge.close_client()
    client = mango_bridge._client()
    assert client.timeout.read == 5.0
    assert client.base_url.host == "mango.test"


def test_close_client_is_idempotent_without_a_built_client() -> None:
    mango_bridge.close_client()  # no client cached yet
    mango_bridge.close_client()  # still a no-op, must not raise
