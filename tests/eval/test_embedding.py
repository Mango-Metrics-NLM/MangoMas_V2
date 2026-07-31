"""Tests for the embedding scorer (capability-gap path + cosine math)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest

from mangomas.errors import ConfigError
from mangomas.eval.protocol import ScorerContext
from mangomas.eval.registry import scorer_registry
from mangomas.eval.scorers.embedding import (
    DEFAULT_EMBEDDING_THRESHOLD,
    EmbeddingScorer,
    _cosine_similarity,
)
from tests.fakes import FakeEmbeddingClient, FakeLLM


@dataclass
class _EmbeddingFakeLLM(FakeLLM):
    """``FakeLLM`` extended with a deterministic ``embed()`` method."""

    async def embed(self, text: str) -> list[float]:
        # Embedding for "x" is its character ordinals; deterministic + finite.
        return [float(ord(c)) for c in text] or [0.0]


async def test_embedding_scorer_raises_when_llm_lacks_embed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    scorer = EmbeddingScorer()
    with (
        caplog.at_level(logging.WARNING, logger="mangomas.eval.scorers.embedding"),
        pytest.raises(NotImplementedError),
    ):
        await scorer.score(
            "prediction",
            "expected",
            context=ScorerContext(llm=FakeLLM()),
        )
    matching = [
        rec
        for rec in caplog.records
        if getattr(rec, "event", None) == "embedding_scorer_unavailable"
    ]
    assert matching, "expected an embedding_scorer_unavailable warning"


async def test_embedding_scorer_raises_when_context_is_none() -> None:
    scorer = EmbeddingScorer()
    with pytest.raises(NotImplementedError):
        await scorer.score("a", "b", context=None)


async def test_embedding_scorer_uses_dedicated_embeddings_client() -> None:
    scorer = EmbeddingScorer(threshold=0.0)
    result = await scorer.score(
        "abc",
        "abc",
        context=ScorerContext(embeddings=FakeEmbeddingClient()),
    )
    assert result.score == pytest.approx(1.0)
    assert result.passed is True


async def test_embedding_scorer_prefers_embeddings_over_llm() -> None:
    """When both are present, the dedicated embeddings client wins."""
    scorer = EmbeddingScorer(threshold=0.0)
    embedder = FakeEmbeddingClient()
    result = await scorer.score(
        "abc",
        "abc",
        context=ScorerContext(llm=FakeLLM(), embeddings=embedder),
    )
    assert result.passed is True
    # The dedicated client was the one invoked (twice: prediction + expected).
    assert embedder.calls == [["abc"], ["abc"]]


async def test_embedding_scorer_with_real_embed_passes() -> None:
    scorer = EmbeddingScorer(threshold=0.0)  # any non-zero similarity passes
    result = await scorer.score(
        "abc",
        "abc",
        context=ScorerContext(llm=_EmbeddingFakeLLM()),
    )
    # Identical input -> cosine = 1.0 -> normalised score = 1.0.
    assert result.score == pytest.approx(1.0)
    assert result.passed is True
    assert result.metadata["cosine_similarity"] == pytest.approx(1.0)


async def test_embedding_scorer_fails_below_threshold() -> None:
    # Length-mismatched vectors force cosine=0.0 (see _cosine_similarity), so
    # the normalised score is 0.5 — comfortably below the 0.99 threshold.
    scorer = EmbeddingScorer(threshold=0.99)
    result = await scorer.score(
        "a",
        "bb",
        context=ScorerContext(llm=_EmbeddingFakeLLM()),
    )
    assert result.passed is False
    assert result.metadata["cosine_similarity"] == 0.0


def test_cosine_similarity_zero_vector() -> None:
    assert _cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


def test_cosine_similarity_empty_vector() -> None:
    assert _cosine_similarity([], [1.0]) == 0.0


def test_cosine_similarity_length_mismatch() -> None:
    assert _cosine_similarity([1.0, 2.0], [1.0]) == 0.0


def test_cosine_similarity_identical() -> None:
    assert _cosine_similarity([1.0, 2.0], [1.0, 2.0]) == pytest.approx(1.0)


def test_embedding_scorer_rejects_invalid_threshold() -> None:
    # ConfigError (not ValueError): unified onto the shared eval option-error
    # taxonomy via `require_unit_float` (mangomas.eval._options).
    with pytest.raises(ConfigError):
        EmbeddingScorer(threshold=-0.1)


def test_embedding_factory_uses_default_threshold() -> None:
    scorer = scorer_registry.get("embedding")({})
    assert isinstance(scorer, EmbeddingScorer)
    assert scorer._threshold == DEFAULT_EMBEDDING_THRESHOLD
