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
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from mangomas.config import (
    DEFAULT_ERROR_DETAIL_TRUNCATE,
    DEFAULT_EVAL_JUDGE_CALIBRATION_MAX_AGE_DAYS,
    DEFAULT_EVAL_JUDGE_MIN_KAPPA,
    DEFAULT_EVAL_JUDGE_MIN_RHO,
    DEFAULT_EVAL_JUDGE_MIN_SAMPLE_SIZE,
)
from mangomas.core.agent import Message
from mangomas.core.structured import parse_or_recover
from mangomas.errors import ConfigError, LLMBadResponse
from mangomas.eval._options import coerce_float, require_bool, require_unit_float
from mangomas.eval.judge_calibration import (
    CalibrationPolicy,
    JudgeStatus,
    assess_calibration,
    load_calibration_record,
    status_summary_fields,
)
from mangomas.eval.protocol import Scorer, ScorerContext, ScoreResult
from mangomas.eval.provenance import digest_of
from mangomas.eval.registry import scorer_registry

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.adapters.llm.base import LLMClient

logger = logging.getLogger(__name__)


DEFAULT_JUDGE_THRESHOLD: float = 0.7

_JUDGE_INSTRUCTION = (
    "You are a strict but fair evaluator. Given a model PREDICTION and an "
    "EXPECTED answer, score the prediction on a [0.0, 1.0] scale where "
    "1.0 means semantically equivalent to expected and 0.0 means clearly wrong. "
)
_JUDGE_INSTRUCTION_WITH_INPUT = (
    "You are a strict but fair evaluator. Given the INPUT a model was asked to "
    "answer, its PREDICTION and an EXPECTED answer, score the prediction on a "
    "[0.0, 1.0] scale where 1.0 means it answers the input as well as expected "
    "does and 0.0 means clearly wrong. "
)
_JUDGE_FORMAT = (
    "Respond with ONLY a single JSON object on one line, exactly matching: "
    '{"score": <float between 0.0 and 1.0>, "rationale": "<short explanation>"}'
)
#: The prompt with every spec-0048 option at its default — byte-identical to
#: the prompt this scorer always sent, so an unconfigured judge is unchanged.
_JUDGE_SYSTEM_PROMPT = _JUDGE_INSTRUCTION + _JUDGE_FORMAT
_JUDGE_PROMPT_VERSION = 1
_NO_INPUT = "(none)"

Rubric = str | list[str] | None


def build_judge_system_prompt(*, rubric: Rubric = None, include_input: bool = False) -> str:
    """The judge's system prompt for the given options (spec-0048).

    A rubric is rendered as a criteria block between the instruction and the
    output format, so the one-line JSON contract is always the last thing the
    judge reads.
    """
    instruction = _JUDGE_INSTRUCTION_WITH_INPUT if include_input else _JUDGE_INSTRUCTION
    if rubric is None:
        return instruction + _JUDGE_FORMAT
    criteria = [rubric] if isinstance(rubric, str) else list(rubric)
    block = "\n".join(f"- {criterion}" for criterion in criteria)
    return (
        f"{instruction}\nRUBRIC (judge the prediction against every criterion; the "
        f"score is your overall judgement across them):\n{block}\n{_JUDGE_FORMAT}"
    )


def judge_prompt_sha256(*, rubric: Rubric = None, include_input: bool = False) -> str:
    """Digest of what the judge is asked — the key a calibration record pins (spec-0048)."""
    return digest_of(
        {
            "version": _JUDGE_PROMPT_VERSION,
            "system": build_judge_system_prompt(rubric=rubric, include_input=include_input),
            "include_input": include_input,
        }
    )


def _render_input(messages: list[Message]) -> str:
    if not messages:
        return _NO_INPUT
    return "\n".join(f"[{message.role}] {message.content}" for message in messages)


class _JudgeEnvelope(BaseModel):
    """Permissive shape for recovering a judge reply wrapped in prose or a fence.

    Only ``score`` is required; its *value* is validated by the same checks as
    a strictly parsed reply, so recovery widens formatting tolerance, never
    what counts as a score.
    """

    model_config = ConfigDict(extra="allow")

    score: Any
    rationale: Any = ""


class LLMJudgeScorer:
    """Score with an LLM judge: the shared client by default, or a named profile.

    spec-0048 options: ``rubric`` (criteria rendered into the system prompt),
    ``include_input`` (the judge also sees the row's input messages, system
    prompt included) and ``judge_profile`` (grade through a provider profile
    rather than the system under test's own client). All default off, and the
    default prompt is byte-identical to the historical one.
    """

    name = "llm_judge"

    def __init__(
        self,
        *,
        threshold: float = DEFAULT_JUDGE_THRESHOLD,
        rubric: Rubric = None,
        include_input: bool = False,
        judge_profile: str | None = None,
        judge_status: JudgeStatus | None = None,
    ) -> None:
        self._threshold = require_unit_float(threshold, owner=self.name)
        self._include_input = include_input
        self._judge_profile = judge_profile
        self._system_prompt = build_judge_system_prompt(rubric=rubric, include_input=include_input)
        self._prompt_sha256 = judge_prompt_sha256(rubric=rubric, include_input=include_input)
        self._announced = False
        # spec-0047: a judge is uncalibrated until a record says otherwise —
        # constructing one without a status never reads as calibrated.
        self._judge_status: JudgeStatus = judge_status or assess_calibration(
            None,
            judge_model=None,
            prompt_sha256=self._prompt_sha256,
            judge_profile=judge_profile,
        )

    @property
    def judge_status(self) -> JudgeStatus:
        """Calibration status the report carries (``JudgeAware``)."""
        return self._judge_status

    @property
    def prompt_sha256(self) -> str:
        """Digest of this judge's prompt; a calibration record must name it."""
        return self._prompt_sha256

    def _client(self, context: ScorerContext) -> LLMClient:
        if self._judge_profile is None:
            if context.llm is None:
                raise LLMBadResponse(
                    f"{self.name} requires a ScorerContext with an LLM client; "
                    "the EvalRunner is expected to inject one from AgentContext."
                )
            return context.llm
        client = context.llm_profiles.get(self._judge_profile)
        if client is None:
            raise ConfigError(
                f"{self.name}: judge_profile {self._judge_profile!r} is not a built LLM "
                f"profile; available: {sorted(context.llm_profiles)}"
            )
        return client

    def _announce(self) -> None:
        """One line per instance saying whose model grades, with the calibration status."""
        if self._announced:
            return
        self._announced = True
        fields = {"scorer": self.name, **status_summary_fields(self._judge_status)}
        if self._judge_profile is not None:
            logger.info(
                "llm_judge grades through LLM profile %r; calibration: %s",
                self._judge_profile,
                self._judge_status.summary(),
                extra={"event": "eval_llm_judge_profile", "judge_profile": self._judge_profile}
                | fields,
            )
            return
        # ``ScorerContext.llm`` is the system under test's own client, so the
        # judge grades its own provider; spec-0047 demotes its scores to
        # "reported, not gated" unless a calibration record covers it.
        logger.warning(
            "llm_judge grades with the system-under-test's own LLM client (self-grading); "
            "calibration: %s",
            self._judge_status.summary(),
            extra={"event": "eval_llm_judge_self_grading"} | fields,
        )

    def _messages(self, prediction: str, expected: str, context: ScorerContext) -> list[Message]:
        body = f"PREDICTION:\n{prediction}\n\nEXPECTED:\n{expected}"
        if self._include_input:
            body = f"INPUT:\n{_render_input(list(context.messages))}\n\n{body}"
        return [
            Message(role="system", content=self._system_prompt),
            Message(role="user", content=body),
        ]

    def _parse(self, raw: str) -> tuple[dict[str, Any], str]:
        """Strict JSON first; else recover a wrapped object (spec-0048)."""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            recovered = parse_or_recover(raw, _JudgeEnvelope)
            if recovered is None:
                raise LLMBadResponse(
                    f"{self.name}: judge response is not JSON",
                    detail=raw[:DEFAULT_ERROR_DETAIL_TRUNCATE],
                ) from None
            return recovered.model_dump(), "recovered"
        if not isinstance(payload, dict) or "score" not in payload:
            raise LLMBadResponse(
                f"{self.name}: judge response missing 'score' field",
                detail=str(payload)[:DEFAULT_ERROR_DETAIL_TRUNCATE],
            )
        return payload, "strict"

    async def score(
        self,
        prediction: str,
        expected: str,
        *,
        context: ScorerContext | None = None,
    ) -> ScoreResult:
        if context is None:
            raise LLMBadResponse(
                f"{self.name} requires a ScorerContext with an LLM client; "
                "the EvalRunner is expected to inject one from AgentContext."
            )
        client = self._client(context)
        self._announce()
        logger.debug(
            "LLM judge request",
            extra={
                "event": "eval_llm_judge_request",
                "scorer": self.name,
                "threshold": self._threshold,
                "judge_profile": self._judge_profile,
                "prompt_sha256": self._prompt_sha256,
            },
        )
        raw = await client.complete(self._messages(prediction, expected, context))
        payload, parse_mode = self._parse(raw)
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
                "parse": parse_mode,
            },
        )


def _positive_int(options: dict[str, Any], key: str, default: int) -> int:
    raw = options.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ConfigError(f"llm_judge '{key}' must be an integer >= 1; got {raw!r}")
    return raw


def _rubric_option(options: dict[str, Any]) -> Rubric:
    raw = options.get("rubric")
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if (
        isinstance(raw, list)
        and raw
        and all(isinstance(item, str) and item.strip() for item in raw)
    ):
        return [item.strip() for item in raw]
    raise ConfigError(
        f"llm_judge 'rubric' must be a non-empty string or list of strings; got {raw!r}"
    )


def _profile_option(options: dict[str, Any]) -> str | None:
    raw = options.get("judge_profile")
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    raise ConfigError(f"llm_judge 'judge_profile' must be a non-empty string; got {raw!r}")


def judge_status_from_options(
    options: dict[str, Any], *, prompt_sha256: str | None = None
) -> JudgeStatus:
    """Resolve the calibration status from factory options (spec-0047 / spec-0048).

    ``calibration_path`` (``None`` = no record), ``judge_model`` (the judging
    client's model id — the CLI seeds the shared ``MANGOMAS_LLM__MODEL`` or the
    ``judge_profile``'s model), ``judge_profile``, and the policy floors
    ``min_sample_size`` / ``min_kappa`` / ``min_rho`` / ``max_age_days``.
    *prompt_sha256* is the digest of the prompt the judge will send. A
    set-but-unreadable path is a :class:`ConfigError`.
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
    return assess_calibration(
        record,
        judge_model=model,
        policy=policy,
        prompt_sha256=prompt_sha256,
        judge_profile=_profile_option(options),
    )


def _llm_judge_factory(options: dict[str, Any]) -> Scorer:
    threshold = coerce_float(
        options.get("threshold", DEFAULT_JUDGE_THRESHOLD), owner="llm_judge", field="threshold"
    )
    rubric = _rubric_option(options)
    include_input = require_bool(options, "include_input", default=False, owner="llm_judge")
    status = judge_status_from_options(
        options, prompt_sha256=judge_prompt_sha256(rubric=rubric, include_input=include_input)
    )
    return LLMJudgeScorer(
        threshold=threshold,
        rubric=rubric,
        include_input=include_input,
        judge_profile=_profile_option(options),
        judge_status=status,
    )


scorer_registry.register("llm_judge", _llm_judge_factory)
