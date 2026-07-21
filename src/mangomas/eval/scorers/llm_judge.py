"""LLM-as-judge scorer.

Asks the configured LLM to score a prediction against an expected answer
on a ``[0.0, 1.0]`` scale, returning a structured JSON envelope:
``{"score": <float>, "rationale": "<text>"}``.

Pass / fail uses ``score >= threshold`` (default ``0.7``). Malformed
responses raise :class:`~mangomas.errors.LLMBadResponse` so the runner can
mark the row as a scoring failure rather than silently scoring ``0``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE
from mangomas.core.agent import Message
from mangomas.errors import LLMBadResponse
from mangomas.eval.protocol import Scorer, ScorerContext, ScoreResult
from mangomas.eval.registry import scorer_registry

logger = logging.getLogger(__name__)


DEFAULT_JUDGE_THRESHOLD: float = 0.7

_JUDGE_SYSTEM_PROMPT = (
    "You are a strict but fair evaluator. Given a model PREDICTION and an "
    "EXPECTED answer, score the prediction on a [0.0, 1.0] scale where "
    "1.0 means semantically equivalent to expected and 0.0 means clearly wrong. "
    "Respond with ONLY a single JSON object on one line, exactly matching: "
    '{"score": <float between 0.0 and 1.0>, "rationale": "<short explanation>"}'
)


def _build_judge_messages(prediction: str, expected: str) -> list[Message]:
    return [
        Message(role="system", content=_JUDGE_SYSTEM_PROMPT),
        Message(
            role="user",
            content=f"PREDICTION:\n{prediction}\n\nEXPECTED:\n{expected}",
        ),
    ]


class LLMJudgeScorer:
    """Score using the same LLM provider configured for the orchestrator."""

    name = "llm_judge"

    def __init__(self, *, threshold: float = DEFAULT_JUDGE_THRESHOLD) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"threshold must be in [0.0, 1.0]; got {threshold}")
        self._threshold = threshold

    async def score(
        self,
        prediction: str,
        expected: str,
        *,
        context: ScorerContext | None = None,
    ) -> ScoreResult:
        if context is None or context.llm is None:
            raise LLMBadResponse(
                f"{self.name} requires a ScorerContext with an LLM client; "
                "the EvalRunner is expected to inject one from AgentContext."
            )
        messages = _build_judge_messages(prediction, expected)
        logger.debug(
            "LLM judge request",
            extra={
                "event": "eval_llm_judge_request",
                "scorer": self.name,
                "threshold": self._threshold,
            },
        )
        raw = await context.llm.complete(messages)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMBadResponse(
                f"{self.name}: judge response is not JSON",
                detail=raw[:DEFAULT_ERROR_DETAIL_TRUNCATE],
            ) from exc
        if not isinstance(payload, dict) or "score" not in payload:
            raise LLMBadResponse(
                f"{self.name}: judge response missing 'score' field",
                detail=str(payload)[:DEFAULT_ERROR_DETAIL_TRUNCATE],
            )
        try:
            score = float(payload["score"])
        except (TypeError, ValueError) as exc:
            raise LLMBadResponse(
                f"{self.name}: judge 'score' is not numeric",
                detail=str(payload.get("score"))[:DEFAULT_ERROR_DETAIL_TRUNCATE],
            ) from exc
        # Clamp into [0,1] so a sloppy judge response can still produce a valid
        # ScoreResult; record the unclamped value in metadata for auditability.
        clamped = max(0.0, min(1.0, score))
        return ScoreResult(
            score=clamped,
            passed=clamped >= self._threshold,
            metadata={
                "scorer": self.name,
                "threshold": self._threshold,
                "raw_score": score,
                "rationale": payload.get("rationale", ""),
            },
        )


def _llm_judge_factory(options: dict[str, Any]) -> Scorer:
    threshold = float(options.get("threshold", DEFAULT_JUDGE_THRESHOLD))
    return LLMJudgeScorer(threshold=threshold)


scorer_registry.register("llm_judge", _llm_judge_factory)
