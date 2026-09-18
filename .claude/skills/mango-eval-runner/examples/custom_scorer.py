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
            "Scoring outputs",
            extra={"expected_length": len(expected), "prediction_length": len(prediction)},
        )

        # Replace with a real similarity calculation (e.g. embedding cosine).
        # We use a simple length-ratio fallback for this example.
        if not expected and not prediction:
            similarity = 1.0
        elif not expected or not prediction:
            similarity = 0.0
        else:
            similarity = min(len(expected), len(prediction)) / max(len(expected), len(prediction))
            
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


# Do not register at import-time. The CLI discovers scorers via entry points.
# Add to your pyproject.toml:
# [project.entry-points."mangomas.eval.scorers"]
# semantic_similarity = "my_package.my_module:_semantic_similarity_factory"
