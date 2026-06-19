"""Substring-containment scorer.

``score == 1.0`` iff the per-row ``expected`` string is a substring of
``prediction``. Like ``regex_match``, this overloads ``expected`` — here it is
the *needle*, not a gold answer.

Options (via ``EvalSettings.scorer_options``):

``case_sensitive`` (bool, default ``False``)
    When ``False``, both strings are lower-cased before the containment check.
"""

from __future__ import annotations

import logging
from typing import Any

from mangomas.eval.protocol import Scorer, ScorerContext, ScoreResult
from mangomas.eval.registry import scorer_registry

logger = logging.getLogger(__name__)


class ContainsScorer:
    """Pass iff ``expected`` is contained in ``prediction``."""

    name = "contains"

    def __init__(self, *, case_sensitive: bool = False) -> None:
        self._case_sensitive = case_sensitive

    async def score(
        self,
        prediction: str,
        expected: str,
        *,
        context: ScorerContext | None = None,  # noqa: ARG002
    ) -> ScoreResult:
        haystack = prediction if self._case_sensitive else prediction.lower()
        needle = expected if self._case_sensitive else expected.lower()
        is_match = needle in haystack
        return ScoreResult(
            score=1.0 if is_match else 0.0,
            passed=is_match,
            metadata={"scorer": self.name, "case_sensitive": self._case_sensitive},
        )


def _contains_factory(options: dict[str, Any]) -> Scorer:
    return ContainsScorer(case_sensitive=bool(options.get("case_sensitive", False)))


scorer_registry.register("contains", _contains_factory)
