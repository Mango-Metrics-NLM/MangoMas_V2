"""Direct unit tests for the shared OpenAI-compatible client lifecycle base.

These lock the reusable contract in :mod:`mangomas.adapters._openai_client`
that the LM Studio chat and embedding adapters both inherit: base-URL
normalisation, injected-client ownership, and error-translation binding.
"""

from __future__ import annotations

import httpx

from mangomas.adapters._openai_client import OpenAICompatHTTPClient
from mangomas.errors import LLMBadResponse, LLMTimeout, LLMUnavailable
from tests.constants import TEST_LMSTUDIO_MOCK_BASE_URL, TEST_LMSTUDIO_MOCK_MODEL

_LABEL = "Test upstream"
_TIMEOUT_SECONDS = 5.0
_API_KEY = "test-key"


class _BadResponse(LLMBadResponse):
    """Distinguishable bad-response subtype supplied by a subclass."""


class _Client(OpenAICompatHTTPClient):
    _LABEL = _LABEL
    _BAD_RESPONSE = _BadResponse


def _make(client: httpx.AsyncClient | None = None) -> _Client:
    return _Client(
        TEST_LMSTUDIO_MOCK_BASE_URL,
        TEST_LMSTUDIO_MOCK_MODEL,
        _API_KEY,
        _TIMEOUT_SECONDS,
        client,
    )


def test_base_url_is_normalised() -> None:
    adapter = _Client(
        f"{TEST_LMSTUDIO_MOCK_BASE_URL}/", TEST_LMSTUDIO_MOCK_MODEL, _API_KEY, _TIMEOUT_SECONDS
    )
    assert adapter._base_url == TEST_LMSTUDIO_MOCK_BASE_URL


async def test_owned_client_is_closed_on_aclose() -> None:
    adapter = _make()
    assert adapter._owns_client
    await adapter.aclose()
    assert adapter._client.is_closed


async def test_injected_client_is_not_closed_on_aclose() -> None:
    injected = httpx.AsyncClient()
    adapter = _make(injected)
    assert not adapter._owns_client
    await adapter.aclose()
    assert not injected.is_closed
    await injected.aclose()


def test_owned_client_carries_bearer_auth() -> None:
    adapter = _make()
    assert adapter._client.headers["Authorization"] == f"Bearer {_API_KEY}"


def test_translate_error_binds_label_and_subtype() -> None:
    adapter = _make()

    timeout = adapter._translate_error(httpx.ReadTimeout("slow"))
    assert isinstance(timeout, LLMTimeout)
    assert _LABEL in str(timeout)

    request = httpx.Request("POST", f"{TEST_LMSTUDIO_MOCK_BASE_URL}/chat/completions")
    response = httpx.Response(500, request=request)
    status = adapter._translate_error(
        httpx.HTTPStatusError("server error", request=request, response=response)
    )
    assert isinstance(status, _BadResponse)

    other = adapter._translate_error(httpx.ConnectError("refused"))
    assert isinstance(other, LLMUnavailable)
