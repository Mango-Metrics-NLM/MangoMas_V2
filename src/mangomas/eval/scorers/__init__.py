"""Built-in scorer implementations.

Importing this package registers every scorer in :data:`scorer_registry`.
External plugins follow the same pattern.
"""

from __future__ import annotations

from mangomas.eval.scorers.embedding import EmbeddingScorer
from mangomas.eval.scorers.exact_match import ExactMatchScorer
from mangomas.eval.scorers.llm_judge import LLMJudgeScorer

__all__ = ["EmbeddingScorer", "ExactMatchScorer", "LLMJudgeScorer"]
