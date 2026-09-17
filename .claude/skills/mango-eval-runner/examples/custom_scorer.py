"""Custom scorer example for the mango-eval-runner skill."""

from __future__ import annotations

import logging
from typing import Any

from mangomas.eval.protocol import ScoreResult, Scorer, ScorerContext
from mangomas.eval.registry import ScorerFactory, scorer_registry

logger = logging.getLogger(__name__)


class SemanticSimilarityScorer:
    """Custom scorer to evaluate semantic similarity between expected and actual outputs."""

    name: str = "semantic_similarity"

    def __init__(self, threshold: float = 0.8) -> None:
        self.threshold = threshold

    async def score(
        self,
        prediction: str,
        expected: str,
        *,
        context: ScorerContext | None = None,
    ) -> ScoreResult:
        """Calculate the similarity score."""
        logger.debug(
            "Scoring outputs. Expected: %s..., Actual: %s...",
            expected[:20],
            prediction[:20],
        )

        # Replace with a real similarity calculation (e.g. embedding cosine).
        similarity = 0.85
        passed = similarity >= self.threshold

        return ScoreResult(
            score=similarity,
            passed=passed,
            metadata={"threshold_used": self.threshold},
        )


def _semantic_similarity_factory(options: dict[str, Any]) -> Scorer:
    """Factory compatible with scorer_registry's ``ScorerFactory`` signature."""
    return SemanticSimilarityScorer(  # type: ignore[return-value]
        threshold=float(options.get("threshold", 0.8)),
    )


# Register the scorer so it can be discovered by the CLI.
scorer_registry.register("semantic_similarity", _semantic_similarity_factory)
