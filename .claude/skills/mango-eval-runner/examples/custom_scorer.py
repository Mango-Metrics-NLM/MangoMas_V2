from typing import Any
import logging

from mangomas.eval.protocol import ScoreResult, Scorer
from mangomas.eval.registry import scorer_registry

logger = logging.getLogger(__name__)

class SemanticSimilarityScorer(Scorer):
    "\""
    Custom scorer to evaluate semantic similarity between expected and actual outputs.
    "\""
    name: str = "semantic_similarity"
    
    def __init__(self, threshold: float = 0.8) -> None:
        self.threshold = threshold
        
    async def score(self, prediction: str, expected: str, *, context: Any = None) -> ScoreResult:
        "\""
        Calculates the similarity score.
        "\""
        logger.debug(f"Scoring outputs. Expected: {expected[:20]}..., Actual: {prediction[:20]}...")
        
        # Mock similarity calculation
        similarity = 0.85 
        passed = similarity >= self.threshold
        
        return ScoreResult(
            name=self.name,
            score=similarity,
            passed=passed,
            metadata={
                "threshold_used": self.threshold
            }
        )

# Register the scorer so it can be used in the CLI
scorer_registry.register("semantic_similarity", SemanticSimilarityScorer)
