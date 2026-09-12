"""Cost / budget scorer.

Estimates USD per row and optionally fails when the estimate exceeds a
per-row budget. ``ScoreResult.score`` stays in ``[0.0, 1.0]`` (the harness
aggregate); the dollar figure lives in ``metadata[cost_usd]`` so a mean can
be gated separately via ``EvalSettings.max_mean_cost_usd``.

Measure-only (default: ``max_cost_usd=None``) always passes — Kapoor-style
cost is a *dimension*, not a silent quality override.
"""

from __future__ import annotations

import logging
from typing import Any

from mangomas.config import (
    DEFAULT_EVAL_COST_MAX_USD,
    DEFAULT_EVAL_COST_USD_PER_1K_INPUT_TOKENS,
    DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_CHARS,
    DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_TOKENS,
    EVAL_COST_INPUT_TOKENS_METADATA_KEY,
    EVAL_COST_OUTPUT_TOKENS_METADATA_KEY,
    EVAL_COST_USD_METADATA_KEY,
)
from mangomas.eval._options import require_non_negative_float
from mangomas.eval.protocol import Scorer, ScorerContext, ScoreResult
from mangomas.eval.registry import scorer_registry

logger = logging.getLogger(__name__)

_COST_SOURCE_EXPLICIT: str = "explicit"
_COST_SOURCE_TOKENS: str = "tokens"
_COST_SOURCE_OUTPUT_CHARS: str = "output_chars"
_TOKENS_PER_THOUSAND: float = 1000.0


def _metadata_non_negative_float(metadata: dict[str, Any], key: str) -> float | None:
    raw = metadata.get(key)
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    number = float(raw)
    if number < 0.0:
        return None
    return number


def estimate_cost_usd(
    prediction: str,
    *,
    metadata: dict[str, Any],
    usd_per_1k_input_tokens: float,
    usd_per_1k_output_tokens: float,
    usd_per_1k_output_chars: float,
) -> tuple[float, str]:
    """Return ``(cost_usd, source)`` for *prediction* given *metadata*.

    Precedence: explicit ``cost_usd`` > token counts > output-character rate.
    """
    explicit = _metadata_non_negative_float(metadata, EVAL_COST_USD_METADATA_KEY)
    if explicit is not None:
        return explicit, _COST_SOURCE_EXPLICIT
    input_tokens = _metadata_non_negative_float(metadata, EVAL_COST_INPUT_TOKENS_METADATA_KEY)
    output_tokens = _metadata_non_negative_float(metadata, EVAL_COST_OUTPUT_TOKENS_METADATA_KEY)
    if input_tokens is not None or output_tokens is not None:
        in_tok = input_tokens or 0.0
        out_tok = output_tokens or 0.0
        cost = (
            in_tok / _TOKENS_PER_THOUSAND * usd_per_1k_input_tokens
            + out_tok / _TOKENS_PER_THOUSAND * usd_per_1k_output_tokens
        )
        return cost, _COST_SOURCE_TOKENS
    cost = len(prediction) / _TOKENS_PER_THOUSAND * usd_per_1k_output_chars
    return cost, _COST_SOURCE_OUTPUT_CHARS


class CostBudgetScorer:
    """Pass unless a per-row USD budget is set and the estimate exceeds it."""

    name = "cost_budget"

    def __init__(
        self,
        *,
        max_cost_usd: float | None = DEFAULT_EVAL_COST_MAX_USD,
        usd_per_1k_input_tokens: float = DEFAULT_EVAL_COST_USD_PER_1K_INPUT_TOKENS,
        usd_per_1k_output_tokens: float = DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_TOKENS,
        usd_per_1k_output_chars: float = DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_CHARS,
    ) -> None:
        self._max_cost_usd = max_cost_usd
        self._usd_per_1k_input_tokens = usd_per_1k_input_tokens
        self._usd_per_1k_output_tokens = usd_per_1k_output_tokens
        self._usd_per_1k_output_chars = usd_per_1k_output_chars

    async def score(
        self,
        prediction: str,
        expected: str,  # noqa: ARG002 — cost is independent of the gold string
        *,
        context: ScorerContext | None = None,
    ) -> ScoreResult:
        metadata = dict(context.row_metadata) if context is not None else {}
        cost_usd, source = estimate_cost_usd(
            prediction,
            metadata=metadata,
            usd_per_1k_input_tokens=self._usd_per_1k_input_tokens,
            usd_per_1k_output_tokens=self._usd_per_1k_output_tokens,
            usd_per_1k_output_chars=self._usd_per_1k_output_chars,
        )
        within_budget = self._max_cost_usd is None or cost_usd <= self._max_cost_usd
        logger.debug(
            "Eval cost estimated",
            extra={
                "event": "eval_cost_estimated",
                "scorer": self.name,
                "cost_usd": cost_usd,
                "source": source,
                "max_cost_usd": self._max_cost_usd,
                "passed": within_budget,
            },
        )
        return ScoreResult(
            score=1.0 if within_budget else 0.0,
            passed=within_budget,
            metadata={
                "scorer": self.name,
                EVAL_COST_USD_METADATA_KEY: cost_usd,
                "source": source,
                "max_cost_usd": self._max_cost_usd,
            },
        )


def _optional_max_cost_usd(options: dict[str, Any]) -> float | None:
    if "max_cost_usd" not in options:
        return DEFAULT_EVAL_COST_MAX_USD
    raw = options["max_cost_usd"]
    if raw is None:
        return None
    return require_non_negative_float(raw, owner="cost_budget", field="max_cost_usd")


def _cost_budget_factory(options: dict[str, Any]) -> Scorer:
    return CostBudgetScorer(
        max_cost_usd=_optional_max_cost_usd(options),
        usd_per_1k_input_tokens=require_non_negative_float(
            options.get(
                "usd_per_1k_input_tokens",
                DEFAULT_EVAL_COST_USD_PER_1K_INPUT_TOKENS,
            ),
            owner="cost_budget",
            field="usd_per_1k_input_tokens",
        ),
        usd_per_1k_output_tokens=require_non_negative_float(
            options.get(
                "usd_per_1k_output_tokens",
                DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_TOKENS,
            ),
            owner="cost_budget",
            field="usd_per_1k_output_tokens",
        ),
        usd_per_1k_output_chars=require_non_negative_float(
            options.get(
                "usd_per_1k_output_chars",
                DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_CHARS,
            ),
            owner="cost_budget",
            field="usd_per_1k_output_chars",
        ),
    )


scorer_registry.register("cost_budget", _cost_budget_factory)
