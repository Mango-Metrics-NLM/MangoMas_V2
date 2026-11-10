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
import math
from typing import Any

from mangomas.config import (
    DEFAULT_ERROR_DETAIL_TRUNCATE,
    DEFAULT_EVAL_JUDGE_CALIBRATION_MAX_AGE_DAYS,
    DEFAULT_EVAL_JUDGE_MIN_KAPPA,
    DEFAULT_EVAL_JUDGE_MIN_RHO,
    DEFAULT_EVAL_JUDGE_MIN_SAMPLE_SIZE,
)
from mangomas.core.agent import Message
from mangomas.errors import ConfigError, LLMBadResponse
from mangomas.eval._options import coerce_float, require_unit_float
from mangomas.eval.judge_calibration import (
    CalibrationPolicy,
    JudgeStatus,
    assess_calibration,
    load_calibration_record,
    status_summary_fields,
)
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

    def __init__(
        self,
        *,
        threshold: float = DEFAULT_JUDGE_THRESHOLD,
        judge_status: JudgeStatus | None = None,
    ) -> None:
        self._threshold = require_unit_float(threshold, owner=self.name)
        self._warned_self_grading = False
        # spec-0047: a judge is uncalibrated until a record says otherwise —
        # constructing one without a status never reads as calibrated.
        self._judge_status: JudgeStatus = judge_status or assess_calibration(None, judge_model=None)

    @property
    def judge_status(self) -> JudgeStatus:
        """Calibration status the report carries (``JudgeAware``)."""
        return self._judge_status

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
        if not self._warned_self_grading:
            # ``ScorerContext.llm`` is the system under test's own client, so
            # the judge grades its own provider; spec-0047 demotes its scores
            # to "reported, not gated" unless a calibration record covers
            # this model. One line per instance either way, with the status.
            self._warned_self_grading = True
            logger.warning(
                "llm_judge grades with the system-under-test's own LLM client (self-grading); "
                "calibration: %s",
                self._judge_status.summary(),
                extra={
                    "event": "eval_llm_judge_self_grading",
                    "scorer": self.name,
                    **status_summary_fields(self._judge_status),
                },
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
        if not math.isfinite(score):
            # ``json.loads`` accepts the non-standard ``NaN`` / ``Infinity``
            # tokens and ``float()`` accepts their string forms; ``min(1.0, nan)``
            # returns 1.0, so an unguarded NaN would score a perfect pass.
            raise LLMBadResponse(
                f"{self.name}: judge 'score' is not finite",
                detail=str(payload.get("score"))[:DEFAULT_ERROR_DETAIL_TRUNCATE],
            )
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


def _positive_int(options: dict[str, Any], key: str, default: int) -> int:
    raw = options.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ConfigError(f"llm_judge '{key}' must be an integer >= 1; got {raw!r}")
    return raw


def judge_status_from_options(options: dict[str, Any]) -> JudgeStatus:
    """Resolve the calibration status from factory options (spec-0047).

    ``calibration_path`` (``None`` = no record), ``judge_model`` (the shared
    client's model id — the CLI seeds it from ``MANGOMAS_LLM__MODEL``), and
    the policy floors ``min_sample_size`` / ``min_kappa`` / ``min_rho`` /
    ``max_age_days``. A set-but-unreadable path is a :class:`ConfigError`.
    """
    path = options.get("calibration_path")
    if path is not None and not isinstance(path, str):
        raise ConfigError(f"llm_judge 'calibration_path' must be a string; got {path!r}")
    model = options.get("judge_model")
    if model is not None and not isinstance(model, str):
        raise ConfigError(f"llm_judge 'judge_model' must be a string; got {model!r}")
    policy = CalibrationPolicy(
        min_sample_size=_positive_int(
            options, "min_sample_size", DEFAULT_EVAL_JUDGE_MIN_SAMPLE_SIZE
        ),
        min_kappa=require_unit_float(
            coerce_float(
                options.get("min_kappa", DEFAULT_EVAL_JUDGE_MIN_KAPPA),
                owner="llm_judge",
                field="min_kappa",
            ),
            owner="llm_judge",
            field="min_kappa",
        ),
        min_rho=require_unit_float(
            coerce_float(
                options.get("min_rho", DEFAULT_EVAL_JUDGE_MIN_RHO),
                owner="llm_judge",
                field="min_rho",
            ),
            owner="llm_judge",
            field="min_rho",
        ),
        max_age_days=_positive_int(
            options, "max_age_days", DEFAULT_EVAL_JUDGE_CALIBRATION_MAX_AGE_DAYS
        ),
    )
    record = load_calibration_record(path) if path else None
    return assess_calibration(record, judge_model=model, policy=policy)


def _llm_judge_factory(options: dict[str, Any]) -> Scorer:
    threshold = coerce_float(
        options.get("threshold", DEFAULT_JUDGE_THRESHOLD), owner="llm_judge", field="threshold"
    )
    return LLMJudgeScorer(threshold=threshold, judge_status=judge_status_from_options(options))


scorer_registry.register("llm_judge", _llm_judge_factory)
