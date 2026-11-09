"""Built-in scorer implementations.

Importing this package registers every scorer in :data:`scorer_registry`.
External plugins follow the same pattern.
"""

from __future__ import annotations

from mangomas.eval.scorers.composite import CompositeScorer
from mangomas.eval.scorers.contains import ContainsScorer
from mangomas.eval.scorers.cost_budget import CostBudgetScorer
from mangomas.eval.scorers.embedding import EmbeddingScorer
from mangomas.eval.scorers.exact_match import ExactMatchScorer
from mangomas.eval.scorers.json_field import JsonFieldScorer
from mangomas.eval.scorers.json_keys import JsonKeysScorer
from mangomas.eval.scorers.llm_judge import LLMJudgeScorer
from mangomas.eval.scorers.regex_match import RegexMatchScorer
from mangomas.eval.scorers.tool_trajectory import ToolTrajectoryScorer

__all__ = [
    "CompositeScorer",
    "ContainsScorer",
    "CostBudgetScorer",
    "EmbeddingScorer",
    "ExactMatchScorer",
    "JsonFieldScorer",
    "JsonKeysScorer",
    "LLMJudgeScorer",
    "RegexMatchScorer",
    "ToolTrajectoryScorer",
]
