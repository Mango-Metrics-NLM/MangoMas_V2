"""Tests for the LM Studio embedding adapter (respx-mocked httpx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from mangomas.adapters.embeddings.lmstudio import (
    LMStudioEmbeddingClient,
    LMStudioEmbeddingError,
)
from mangomas.errors import LLMTimeout, LLMUnavailable
from tests.constants import TEST_EMBEDDINGS_MOCK_MODEL, TEST_LMSTUDIO_MOCK_BASE_URL

_EMBEDDINGS_URL = f"{TEST_LMSTUDIO_MOCK_BASE_URL}/embeddings"
_BASE_URL_WITH_TRAILING_SLASH = f"{TEST_LMSTUDIO_MOCK_BASE_URL}/"


def _client(client: httpx.AsyncClient | None = None) -> LMStudioEmbeddingClient:
    return LMStudioEmbeddingClient(
        base_url=TEST_LMSTUDIO_MOCK_BASE_URL,
        model=TEST_EMBEDDINGS_MOCK_MODEL,
        client=client,
    )


@respx.mock
async def test_embed_batch_returns_vectors() -> None:
    route = respx.post(_EMBEDDINGS_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"embedding": [0.1, 0.2, 0.3]}, {"embedding": [0.4, 0.5, 0.6]}]},
        )
    )
    client = _client()
    try:
        out = await client.embed_batch(["a", "b"])
    finally:
        await client.aclose()
    assert out == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert route.called
    sent = route.calls.last.request
    assert b'"input":["a","b"]' in sent.content


@respx.mock
async def test_embed_single_returns_first_vector() -> None:
    respx.post(_EMBEDDINGS_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [1.0, 2.0]}]})
    )
    client = _client()
    try:
        out = await client.embed("hello")
    finally:
        await client.aclose()
    assert out == [1.0, 2.0]


@respx.mock
async def test_embed_raises_on_malformed() -> None:
    respx.post(_EMBEDDINGS_URL).mock(return_value=httpx.Response(200, json={"unexpected": True}))
    client = _client()
    try:
        with pytest.raises(LMStudioEmbeddingError):
            await client.embed("hi")
    finally:
        await client.aclose()


@respx.mock
async def test_embed_raises_on_http_error() -> None:
    respx.post(_EMBEDDINGS_URL).mock(return_value=httpx.Response(500, json={"error": "boom"}))
    client = LMStudioEmbeddingClient(
        base_url=_BASE_URL_WITH_TRAILING_SLASH, model=TEST_EMBEDDINGS_MOCK_MODEL
    )
    try:
        with pytest.raises(LMStudioEmbeddingError):
            await client.embed("hi")
    finally:
        await client.aclose()


@respx.mock
async def test_embed_raises_unavailable_on_connect_error() -> None:
    respx.post(_EMBEDDINGS_URL).mock(side_effect=httpx.ConnectError("refused"))
    client = _client()
    try:
        with pytest.raises(LLMUnavailable):
            await client.embed("hi")
    finally:
        await client.aclose()


@respx.mock
async def test_embed_translates_timeout() -> None:
    respx.post(_EMBEDDINGS_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    client = _client()
    try:
        with pytest.raises(LLMTimeout):
            await client.embed("hi")
    finally:
        await client.aclose()


async def test_external_client_not_closed() -> None:
    async with httpx.AsyncClient() as external:
        client = _client(client=external)
        await client.aclose()
        assert not external.is_closed
