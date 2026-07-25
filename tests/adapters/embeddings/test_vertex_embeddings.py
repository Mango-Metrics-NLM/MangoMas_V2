"""Tests for the Vertex AI embedding adapter (injected fake model client)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from mangomas.adapters.embeddings import vertex as vertex_module
from mangomas.adapters.embeddings.vertex import VertexEmbeddingClient
from mangomas.config import DEFAULT_VERTEX_LOCATION
from mangomas.errors import LLMTimeout, LLMUnavailable
from tests.constants import DEFAULT_EMBEDDINGS_MODEL, TEST_VERTEX_PROJECT


@dataclass
class _FakeEmbedding:
    values: list[float]


class _DeadlineExceeded(Exception):
    """Stand-in whose qualname matches Vertex's timeout-class matrix entry."""


# Force the qualname used by ``_translate_vertex_error``'s timeout set.
_DeadlineExceeded.__module__ = "google.api_core.exceptions"
_DeadlineExceeded.__qualname__ = "DeadlineExceeded"


@dataclass
class _FakeModelClient:
    replies: list[list[float]] = field(default_factory=lambda: [[0.1, 0.2]])
    raise_on_call: BaseException | None = None
    calls: list[list[str]] = field(default_factory=list)

    async def get_embeddings_async(self, texts: list[str]) -> list[Any]:
        self.calls.append(list(texts))
        if self.raise_on_call is not None:
            raise self.raise_on_call
        return [
            _FakeEmbedding(values=self.replies[i % len(self.replies)]) for i in range(len(texts))
        ]


def _client(model: _FakeModelClient) -> VertexEmbeddingClient:
    return VertexEmbeddingClient(
        project_id=TEST_VERTEX_PROJECT,
        location=DEFAULT_VERTEX_LOCATION,
        model=DEFAULT_EMBEDDINGS_MODEL,
        client=model,
    )


async def test_embed_batch_returns_vectors() -> None:
    model = _FakeModelClient(replies=[[1.0, 2.0], [3.0, 4.0]])
    client = _client(model)
    out = await client.embed_batch(["a", "b"])
    assert out == [[1.0, 2.0], [3.0, 4.0]]
    assert model.calls == [["a", "b"]]


async def test_embed_single_returns_first_vector() -> None:
    client = _client(_FakeModelClient(replies=[[5.0, 6.0]]))
    out = await client.embed("a")
    assert out == [5.0, 6.0]


async def test_embed_translates_timeout_error() -> None:
    client = _client(_FakeModelClient(raise_on_call=_DeadlineExceeded("slow")))
    with pytest.raises(LLMTimeout):
        await client.embed("a")


async def test_embed_translates_generic_error_to_unavailable() -> None:
    client = _client(_FakeModelClient(raise_on_call=RuntimeError("boom")))
    with pytest.raises(LLMUnavailable):
        await client.embed("a")


async def test_aclose_is_noop() -> None:
    client = _client(_FakeModelClient())
    await client.aclose()  # no raise


def test_omitting_client_triggers_lazy_sdk_init(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an injected client the adapter lazy-loads the SDK model (ADC path)."""
    seen: dict[str, Any] = {}
    sentinel = _FakeModelClient()

    def _fake_lazy_init(*, project_id: str | None, location: str, model: str) -> Any:
        seen.update(project_id=project_id, location=location, model=model)
        return sentinel

    monkeypatch.setattr(vertex_module, "_lazy_init_model", _fake_lazy_init)
    client = VertexEmbeddingClient(
        project_id=TEST_VERTEX_PROJECT,
        location=DEFAULT_VERTEX_LOCATION,
        model=DEFAULT_EMBEDDINGS_MODEL,
    )

    assert client._client is sentinel
    assert seen == {
        "project_id": TEST_VERTEX_PROJECT,
        "location": DEFAULT_VERTEX_LOCATION,
        "model": DEFAULT_EMBEDDINGS_MODEL,
    }
