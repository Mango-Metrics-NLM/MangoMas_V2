"""Scorer protocol and result types.

A :class:`Scorer` compares a single ``(prediction, expected)`` pair and
returns a :class:`ScoreResult`. The protocol is intentionally minimal so
implementations can be pure-Python (``ExactMatchScorer``), LLM-backed
(``LLMJudgeScorer``), or embedding-based (``EmbeddingScorer``) without
broadening the surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.adapters.llm.base import LLMClient


@dataclass(frozen=True)
class ScoreResult:
    """Outcome of scoring a single prediction.

    ``score`` is normalised to ``[0.0, 1.0]`` so harness aggregation logic
    (mean / min / threshold) is uniform across scorers. ``passed`` lets each
    scorer own its own pass decision — exact-match uses ``score == 1.0``;
    LLM-judge uses ``score >= judge_threshold``; future consensus scorers
    can use voting. ``metadata`` carries scorer-specific extras (rationale,
    embedding similarity, judge tokens, ...).
    """

    score: float
    passed: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(f"ScoreResult.score must be in [0.0, 1.0]; got {self.score}")


@dataclass
class ScorerContext:
    """Per-row context handed to a scorer's ``score()`` call.

    The ``llm`` handle is the orchestrator's existing LLM client — LLM-judge
    and embedding scorers reuse it rather than constructing a parallel
    client. ``row_metadata`` mirrors the dataset row's metadata so scorers
    can branch on per-row hints. ``correlation_id`` propagates from the
    surrounding orchestrator dispatch so scorer logs are correlatable.
    """

    llm: LLMClient | None = None
    row_metadata: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None


@runtime_checkable
class Scorer(Protocol):
    """Single-pair scorer contract."""

    name: str

    async def score(
        self,
        prediction: str,
        expected: str,
        *,
        context: ScorerContext | None = None,
    ) -> ScoreResult:
        """Return a :class:`ScoreResult` for ``prediction`` vs ``expected``."""
        ...
