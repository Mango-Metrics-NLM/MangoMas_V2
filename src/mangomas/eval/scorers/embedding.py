"""Embedding-similarity scorer.

This scorer requires the configured LLM provider to expose an ``embed()``
method returning a list of floats. None of the providers shipped today
(LM Studio, Vertex) do — when invoked against such a provider, the scorer
logs a clear warning and raises :class:`NotImplementedError` so callers
see this as a missing capability, not a runtime LLM failure.

The full implementation will arrive alongside the first embedding-capable
provider; the protocol shape is reserved here so external scorers can be
written against it now.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Protocol, runtime_checkable

from mangomas.eval.protocol import Scorer, ScorerContext, ScoreResult
from mangomas.eval.registry import scorer_registry

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_THRESHOLD: float = 0.75


@runtime_checkable
class _Embeddable(Protocol):
    async def embed(self, text: str) -> list[float]: ...


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Return cosine similarity in ``[-1.0, 1.0]``; zero vector → ``0.0``."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class EmbeddingScorer:
    """Score by cosine similarity of embeddings."""

    name = "embedding"

    def __init__(self, *, threshold: float = DEFAULT_EMBEDDING_THRESHOLD) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"threshold must be in [0.0, 1.0]; got {threshold}")
        self._threshold = threshold

    @staticmethod
    def _resolve_embedder(context: ScorerContext | None) -> _Embeddable | None:
        """Prefer the dedicated embeddings client; fall back to an embed-capable LLM."""
        if context is None:
            return None
        if isinstance(context.embeddings, _Embeddable):
            return context.embeddings
        if isinstance(context.llm, _Embeddable):
            return context.llm
        return None

    async def score(
        self,
        prediction: str,
        expected: str,
        *,
        context: ScorerContext | None = None,
    ) -> ScoreResult:
        embedder = self._resolve_embedder(context)
        if embedder is None:
            logger.warning(
                "Embedding scorer unavailable",
                extra={
                    "event": "embedding_scorer_unavailable",
                    "scorer": self.name,
                    "reason": "no embeddings client and configured LLMClient lacks .embed()",
                },
            )
            raise NotImplementedError(
                "EmbeddingScorer requires an embeddings client (or an LLM with "
                ".embed()); none is configured — enable MANGOMAS_EMBEDDINGS__ENABLED "
                "or see docs/eval/harness.md#known-gaps."
            )
        pred_vec = await embedder.embed(prediction)
        exp_vec = await embedder.embed(expected)
        # Map cosine [-1, 1] to [0, 1] so it fits the ScoreResult contract.
        cosine = _cosine_similarity(pred_vec, exp_vec)
        score = (cosine + 1.0) / 2.0
        return ScoreResult(
            score=score,
            passed=score >= self._threshold,
            metadata={
                "scorer": self.name,
                "threshold": self._threshold,
                "cosine_similarity": cosine,
            },
        )


def _embedding_factory(options: dict[str, Any]) -> Scorer:
    threshold = float(options.get("threshold", DEFAULT_EMBEDDING_THRESHOLD))
    return EmbeddingScorer(threshold=threshold)


scorer_registry.register("embedding", _embedding_factory)
