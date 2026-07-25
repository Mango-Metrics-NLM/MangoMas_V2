"""Direct unit tests for the shared OpenAI-compatible client lifecycle base.

These lock the reusable contract in :mod:`mangomas.adapters._openai_client`
that the LM Studio chat and embedding adapters both inherit: base-URL
normalisation, injected-client ownership, and error-translation binding.
"""

from __future__ import annotations

import inspect

import httpx
import pytest

from mangomas.adapters._openai_client import OpenAICompatHTTPClient
from mangomas.adapters.embeddings._shared import SingleTextEmbedMixin
from mangomas.adapters.embeddings.lmstudio import LMStudioEmbeddingClient
from mangomas.adapters.llm.lmstudio import LMStudioClient
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
        api_key=_API_KEY,
        timeout_seconds=_TIMEOUT_SECONDS,
        client=client,
    )


def test_base_url_is_normalised() -> None:
    adapter = _Client(
        f"{TEST_LMSTUDIO_MOCK_BASE_URL}/",
        TEST_LMSTUDIO_MOCK_MODEL,
        api_key=_API_KEY,
        timeout_seconds=_TIMEOUT_SECONDS,
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


def test_subclass_without_required_class_attrs_fails_at_definition() -> None:
    """A missing _LABEL/_BAD_RESPONSE must fail at import, not inside an error handler."""
    with pytest.raises(TypeError, match="_LABEL"):

        class _Forgot(OpenAICompatHTTPClient):
            pass


def test_base_extra_params_are_keyword_only() -> None:
    """Guards the shared base against a silent positional-parameter reorder."""
    params = inspect.signature(OpenAICompatHTTPClient.__init__).parameters
    positional = [
        name
        for name, p in params.items()
        if name != "self" and p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    ]
    assert positional == ["base_url", "model"]
    for name in ("api_key", "timeout_seconds", "client"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.parametrize(
    ("adapter_cls", "expected_tail"),
    [
        (LMStudioClient, [OpenAICompatHTTPClient, object]),
        (LMStudioEmbeddingClient, [SingleTextEmbedMixin, OpenAICompatHTTPClient, object]),
    ],
)
def test_adapter_mro_reaches_the_shared_base(adapter_cls: type, expected_tail: list[type]) -> None:
    """``super().__init__`` only reaches the base while the MRO tail holds this shape."""
    assert adapter_cls.__mro__[1:] == tuple(expected_tail)


@pytest.mark.parametrize(
    ("adapter_cls", "expected"),
    [
        (
            LMStudioClient,
            ["base_url", "model", "api_key", "timeout_seconds", "default_temperature", "client"],
        ),
        (LMStudioEmbeddingClient, ["base_url", "model", "api_key", "timeout_seconds", "client"]),
    ],
)
def test_public_adapter_constructors_are_unchanged(adapter_cls: type, expected: list[str]) -> None:
    """These signatures are a public contract — the refactor must not reorder them."""
    # Inspect the class, not the bound __init__, so mypy does not flag unsound
    # instance-attribute access on a dynamically-parametrised type.
    params = inspect.signature(adapter_cls).parameters
    assert [name for name in params if name != "self"] == expected
    # Still constructible positionally, the way existing callers may do it.
    assert all(params[name].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for name in expected)


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
