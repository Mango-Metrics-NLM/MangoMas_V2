"""Tests for the LM Studio adapter (using respx to mock httpx)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from mangomas.adapters.llm.lmstudio import LMStudioClient, LMStudioError
from mangomas.core import Message
from mangomas.errors import LLMTimeout, LLMUnavailable
from tests.constants import TEST_LMSTUDIO_MOCK_BASE_URL, TEST_LMSTUDIO_MOCK_MODEL

_CHAT_COMPLETIONS_URL = f"{TEST_LMSTUDIO_MOCK_BASE_URL}/chat/completions"
_MODELS_URL = f"{TEST_LMSTUDIO_MOCK_BASE_URL}/models"
# Used in one test that exercises trailing-slash normalisation in the adapter.
_BASE_URL_WITH_TRAILING_SLASH = f"{TEST_LMSTUDIO_MOCK_BASE_URL}/"


@respx.mock
async def test_complete_returns_content() -> None:
    route = respx.post(_CHAT_COMPLETIONS_URL).mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "hello!"}}]},
        )
    )
    client = LMStudioClient(base_url=TEST_LMSTUDIO_MOCK_BASE_URL, model=TEST_LMSTUDIO_MOCK_MODEL)
    try:
        out = await client.complete([Message(role="user", content="hi")])
    finally:
        await client.aclose()

    assert out == "hello!"
    assert route.called
    sent = route.calls.last.request
    assert b'"model":"m"' in sent.content


@respx.mock
async def test_complete_raises_on_malformed() -> None:
    respx.post(_CHAT_COMPLETIONS_URL).mock(
        return_value=httpx.Response(200, json={"unexpected": True})
    )
    client = LMStudioClient(base_url=TEST_LMSTUDIO_MOCK_BASE_URL, model=TEST_LMSTUDIO_MOCK_MODEL)
    try:
        with pytest.raises(LMStudioError):
            await client.complete([Message(role="user", content="hi")])
    finally:
        await client.aclose()


@respx.mock
async def test_complete_raises_on_http_error() -> None:
    respx.post(_CHAT_COMPLETIONS_URL).mock(return_value=httpx.Response(500, json={"error": "boom"}))
    client = LMStudioClient(base_url=_BASE_URL_WITH_TRAILING_SLASH, model=TEST_LMSTUDIO_MOCK_MODEL)
    try:
        # HTTPStatusError is translated to LMStudioError (a typed LLMBadResponse).
        with pytest.raises(LMStudioError):
            await client.complete([Message(role="user", content="hi")])
    finally:
        await client.aclose()


@respx.mock
async def test_complete_raises_llm_unavailable_on_connect_error() -> None:
    respx.post(_CHAT_COMPLETIONS_URL).mock(side_effect=httpx.ConnectError("connection refused"))
    client = LMStudioClient(base_url=TEST_LMSTUDIO_MOCK_BASE_URL, model=TEST_LMSTUDIO_MOCK_MODEL)
    try:
        with pytest.raises(LLMUnavailable):
            await client.complete([Message(role="user", content="hi")])
    finally:
        await client.aclose()


async def test_external_client_not_closed() -> None:
    async with httpx.AsyncClient() as external:
        client = LMStudioClient(
            base_url=TEST_LMSTUDIO_MOCK_BASE_URL, model=TEST_LMSTUDIO_MOCK_MODEL, client=external
        )
        await client.aclose()
        # External client must remain usable.
        assert not external.is_closed


# ── ping ──────────────────────────────────────────────────────────────────────


@respx.mock
async def test_ping_succeeds() -> None:
    respx.get(_MODELS_URL).mock(return_value=httpx.Response(200, json={"data": []}))
    client = LMStudioClient(base_url=TEST_LMSTUDIO_MOCK_BASE_URL, model=TEST_LMSTUDIO_MOCK_MODEL)
    try:
        await client.ping()  # no raise
    finally:
        await client.aclose()


@respx.mock
async def test_ping_translates_http_error() -> None:
    respx.get(_MODELS_URL).mock(return_value=httpx.Response(503))
    client = LMStudioClient(base_url=TEST_LMSTUDIO_MOCK_BASE_URL, model=TEST_LMSTUDIO_MOCK_MODEL)
    try:
        with pytest.raises(LMStudioError):
            await client.ping()
    finally:
        await client.aclose()


@respx.mock
async def test_ping_translates_connect_error_to_unavailable() -> None:
    respx.get(_MODELS_URL).mock(side_effect=httpx.ConnectError("no route"))
    client = LMStudioClient(base_url=TEST_LMSTUDIO_MOCK_BASE_URL, model=TEST_LMSTUDIO_MOCK_MODEL)
    try:
        with pytest.raises(LLMUnavailable):
            await client.ping()
    finally:
        await client.aclose()


# ── stream ────────────────────────────────────────────────────────────────────


def _sse_body(*chunks: str) -> bytes:
    """Build a synthetic SSE response body from content chunks."""
    lines: list[str] = []
    for c in chunks:
        payload = {"choices": [{"delta": {"content": c}}]}
        lines.append("data: " + json.dumps(payload))
    lines.append("data: [DONE]")
    return ("\n".join(lines) + "\n").encode("utf-8")


@respx.mock
async def test_stream_yields_content_tokens() -> None:
    respx.post(_CHAT_COMPLETIONS_URL).mock(
        return_value=httpx.Response(200, content=_sse_body("Hello", " ", "world"))
    )
    client = LMStudioClient(base_url=TEST_LMSTUDIO_MOCK_BASE_URL, model=TEST_LMSTUDIO_MOCK_MODEL)
    tokens: list[str] = []
    try:
        async for tok in await client.stream([Message(role="user", content="hi")]):
            tokens.append(tok)
    finally:
        await client.aclose()
    assert "".join(tokens) == "Hello world"


@respx.mock
async def test_stream_skips_unparseable_and_non_data_lines() -> None:
    """Lines the parser rejects are skipped without interrupting the token stream."""
    body = (
        ": keep-alive comment\n"
        "event: ping\n"
        "data: not-json\n"
        + "data: "
        + json.dumps({"choices": [{"delta": {"content": "ok"}}]})
        + "\n"
        "data: [DONE]\n"
    ).encode("utf-8")
    respx.post(_CHAT_COMPLETIONS_URL).mock(return_value=httpx.Response(200, content=body))
    client = LMStudioClient(base_url=TEST_LMSTUDIO_MOCK_BASE_URL, model=TEST_LMSTUDIO_MOCK_MODEL)
    tokens: list[str] = []
    try:
        async for tok in await client.stream([Message(role="user", content="hi")]):
            tokens.append(tok)
    finally:
        await client.aclose()
    assert tokens == ["ok"]


@respx.mock
async def test_stream_ends_cleanly_without_done_sentinel() -> None:
    """A truncated stream (no ``[DONE]``) terminates on body exhaustion, not an error."""
    body = ("data: " + json.dumps({"choices": [{"delta": {"content": "partial"}}]}) + "\n").encode(
        "utf-8"
    )
    respx.post(_CHAT_COMPLETIONS_URL).mock(return_value=httpx.Response(200, content=body))
    client = LMStudioClient(base_url=TEST_LMSTUDIO_MOCK_BASE_URL, model=TEST_LMSTUDIO_MOCK_MODEL)
    tokens: list[str] = []
    try:
        async for tok in await client.stream([Message(role="user", content="hi")]):
            tokens.append(tok)
    finally:
        await client.aclose()
    assert tokens == ["partial"]


@respx.mock
async def test_stream_raises_translated_error_on_http_status() -> None:
    respx.post(_CHAT_COMPLETIONS_URL).mock(return_value=httpx.Response(500))
    client = LMStudioClient(base_url=TEST_LMSTUDIO_MOCK_BASE_URL, model=TEST_LMSTUDIO_MOCK_MODEL)
    try:
        with pytest.raises(LMStudioError):
            async for _ in await client.stream([Message(role="user", content="hi")]):
                pass
    finally:
        await client.aclose()


@respx.mock
async def test_stream_raises_unavailable_on_connect_error() -> None:
    respx.post(_CHAT_COMPLETIONS_URL).mock(side_effect=httpx.ConnectError("refused"))
    client = LMStudioClient(base_url=TEST_LMSTUDIO_MOCK_BASE_URL, model=TEST_LMSTUDIO_MOCK_MODEL)
    try:
        with pytest.raises(LLMUnavailable):
            async for _ in await client.stream([Message(role="user", content="hi")]):
                pass
    finally:
        await client.aclose()


# ── SSE line parser ───────────────────────────────────────────────────────────


def test_parse_sse_line_skips_non_data_lines() -> None:
    assert LMStudioClient._parse_sse_line("event: ping") is None


def test_parse_sse_line_returns_done_sentinel() -> None:
    assert LMStudioClient._parse_sse_line("data: [DONE]") == ""


def test_parse_sse_line_skips_unparseable_chunk() -> None:
    assert LMStudioClient._parse_sse_line("data: not-json") is None


def test_parse_sse_line_extracts_content() -> None:
    line = 'data: {"choices":[{"delta":{"content":"x"}}]}'
    assert LMStudioClient._parse_sse_line(line) == "x"


def test_parse_sse_line_skips_empty_content() -> None:
    line = 'data: {"choices":[{"delta":{"content":""}}]}'
    assert LMStudioClient._parse_sse_line(line) is None


# ── timeout translation ───────────────────────────────────────────────────────


@respx.mock
async def test_complete_translates_timeout() -> None:
    respx.post(_CHAT_COMPLETIONS_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    client = LMStudioClient(base_url=TEST_LMSTUDIO_MOCK_BASE_URL, model=TEST_LMSTUDIO_MOCK_MODEL)
    try:
        with pytest.raises(LLMTimeout):
            await client.complete([Message(role="user", content="hi")])
    finally:
        await client.aclose()
