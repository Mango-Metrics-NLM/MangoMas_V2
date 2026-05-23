"""Tests for the LM Studio adapter (using respx to mock httpx)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from mangomas.adapters.llm.lmstudio import LMStudioClient, LMStudioError
from mangomas.core import Message
from mangomas.errors import LLMTimeout, LLMUnavailable


@pytest.mark.asyncio
@respx.mock
async def test_complete_returns_content() -> None:
    route = respx.post("http://lm/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "hello!"}}]},
        )
    )
    client = LMStudioClient(base_url="http://lm/v1", model="m")
    try:
        out = await client.complete([Message(role="user", content="hi")])
    finally:
        await client.aclose()

    assert out == "hello!"
    assert route.called
    sent = route.calls.last.request
    assert b'"model":"m"' in sent.content


@pytest.mark.asyncio
@respx.mock
async def test_complete_raises_on_malformed() -> None:
    respx.post("http://lm/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"unexpected": True})
    )
    client = LMStudioClient(base_url="http://lm/v1", model="m")
    try:
        with pytest.raises(LMStudioError):
            await client.complete([Message(role="user", content="hi")])
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_complete_raises_on_http_error() -> None:
    respx.post("http://lm/v1/chat/completions").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )
    client = LMStudioClient(base_url="http://lm/v1/", model="m")
    try:
        # HTTPStatusError is translated to LMStudioError (a typed LLMBadResponse).
        with pytest.raises(LMStudioError):
            await client.complete([Message(role="user", content="hi")])
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_complete_raises_llm_unavailable_on_connect_error() -> None:
    respx.post("http://lm/v1/chat/completions").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    client = LMStudioClient(base_url="http://lm/v1", model="m")
    try:
        with pytest.raises(LLMUnavailable):
            await client.complete([Message(role="user", content="hi")])
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_external_client_not_closed() -> None:
    async with httpx.AsyncClient() as external:
        client = LMStudioClient(base_url="http://lm/v1", model="m", client=external)
        await client.aclose()
        # External client must remain usable.
        assert not external.is_closed


# ── ping ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_ping_succeeds() -> None:
    respx.get("http://lm/v1/models").mock(return_value=httpx.Response(200, json={"data": []}))
    client = LMStudioClient(base_url="http://lm/v1", model="m")
    try:
        await client.ping()  # no raise
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_ping_translates_http_error() -> None:
    respx.get("http://lm/v1/models").mock(return_value=httpx.Response(503))
    client = LMStudioClient(base_url="http://lm/v1", model="m")
    try:
        with pytest.raises(LMStudioError):
            await client.ping()
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_ping_translates_connect_error_to_unavailable() -> None:
    respx.get("http://lm/v1/models").mock(side_effect=httpx.ConnectError("no route"))
    client = LMStudioClient(base_url="http://lm/v1", model="m")
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


@pytest.mark.asyncio
@respx.mock
async def test_stream_yields_content_tokens() -> None:
    respx.post("http://lm/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=_sse_body("Hello", " ", "world"))
    )
    client = LMStudioClient(base_url="http://lm/v1", model="m")
    tokens: list[str] = []
    try:
        async for tok in await client.stream([Message(role="user", content="hi")]):
            tokens.append(tok)
    finally:
        await client.aclose()
    assert "".join(tokens) == "Hello world"


@pytest.mark.asyncio
@respx.mock
async def test_stream_raises_translated_error_on_http_status() -> None:
    respx.post("http://lm/v1/chat/completions").mock(return_value=httpx.Response(500))
    client = LMStudioClient(base_url="http://lm/v1", model="m")
    try:
        with pytest.raises(LMStudioError):
            async for _ in await client.stream([Message(role="user", content="hi")]):
                pass
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_stream_raises_unavailable_on_connect_error() -> None:
    respx.post("http://lm/v1/chat/completions").mock(side_effect=httpx.ConnectError("refused"))
    client = LMStudioClient(base_url="http://lm/v1", model="m")
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


@pytest.mark.asyncio
@respx.mock
async def test_complete_translates_timeout() -> None:
    respx.post("http://lm/v1/chat/completions").mock(side_effect=httpx.ReadTimeout("slow"))
    client = LMStudioClient(base_url="http://lm/v1", model="m")
    try:
        with pytest.raises(LLMTimeout):
            await client.complete([Message(role="user", content="hi")])
    finally:
        await client.aclose()
