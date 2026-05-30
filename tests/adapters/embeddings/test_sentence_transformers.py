"""Tests for the sentence-transformers embedding adapter (injected fake model)."""

from __future__ import annotations

from dataclasses import dataclass, field

from mangomas.adapters.embeddings.sentence_transformers import (
    SentenceTransformersEmbeddingClient,
)
from tests.constants import DEFAULT_EMBEDDINGS_MODEL


@dataclass
class _FakeModel:
    """Mimics ``SentenceTransformer``: ``encode(texts)`` returns one row per text."""

    calls: list[list[str]] = field(default_factory=list)

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        # Deterministic: each row is the char ordinals of its text.
        return [[float(ord(c)) for c in t] or [0.0] for t in texts]


async def test_embed_batch_uses_injected_model() -> None:
    model = _FakeModel()
    client = SentenceTransformersEmbeddingClient(model=DEFAULT_EMBEDDINGS_MODEL, client=model)
    out = await client.embed_batch(["ab", "c"])
    assert out == [[97.0, 98.0], [99.0]]
    assert model.calls == [["ab", "c"]]


async def test_embed_single_returns_first_vector() -> None:
    model = _FakeModel()
    client = SentenceTransformersEmbeddingClient(model=DEFAULT_EMBEDDINGS_MODEL, client=model)
    out = await client.embed("a")
    assert out == [97.0]


async def test_aclose_is_noop() -> None:
    client = SentenceTransformersEmbeddingClient(
        model=DEFAULT_EMBEDDINGS_MODEL, client=_FakeModel()
    )
    await client.aclose()  # no raise
