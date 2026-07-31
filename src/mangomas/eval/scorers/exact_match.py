"""Exact-string-match scorer.

The simplest possible scorer: ``score == 1.0`` iff ``prediction == expected``
after the configured normalisation (case-folding, whitespace collapse).
"""

from __future__ import annotations

from typing import Any

from mangomas.eval.protocol import Scorer, ScorerContext, ScoreResult
from mangomas.eval.registry import scorer_registry


class ExactMatchScorer:
    """Match ``prediction`` against ``expected`` with optional normalisation.

    Options (passed via :class:`~mangomas.config.EvalSettings.scorer_options`):

    ``case_sensitive`` (default ``False``)
        When ``False``, both strings are lower-cased before comparison.
    ``strip_whitespace`` (default ``True``)
        When ``True``, leading/trailing whitespace is stripped and internal
        runs of whitespace are collapsed to a single space.
    """

    name = "exact_match"

    def __init__(
        self,
        *,
        case_sensitive: bool = False,
        strip_whitespace: bool = True,
    ) -> None:
        self._case_sensitive = case_sensitive
        self._strip_whitespace = strip_whitespace

    def _normalise(self, text: str) -> str:
        out = text
        if self._strip_whitespace:
            out = " ".join(out.split())
        if not self._case_sensitive:
            out = out.lower()
        return out

    async def score(
        self,
        prediction: str,
        expected: str,
        *,
        context: ScorerContext | None = None,  # noqa: ARG002
    ) -> ScoreResult:
        normalised_pred = self._normalise(prediction)
        normalised_exp = self._normalise(expected)
        is_match = normalised_pred == normalised_exp
        return ScoreResult(
            score=1.0 if is_match else 0.0,
            passed=is_match,
            metadata={
                "scorer": self.name,
                "case_sensitive": self._case_sensitive,
                "strip_whitespace": self._strip_whitespace,
            },
        )


def _exact_match_factory(options: dict[str, Any]) -> Scorer:
    return ExactMatchScorer(
        case_sensitive=bool(options.get("case_sensitive", False)),
        strip_whitespace=bool(options.get("strip_whitespace", True)),
    )


scorer_registry.register("exact_match", _exact_match_factory)
