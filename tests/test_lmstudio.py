"""Tests for the LM Studio adapter (using respx to mock httpx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from mangomas.adapters.llm.lmstudio import LMStudioClient, LMStudioError
from mangomas.core import Message


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
        with pytest.raises(httpx.HTTPStatusError):
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
