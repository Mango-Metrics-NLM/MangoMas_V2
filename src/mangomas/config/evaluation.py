"""Evaluation-harness settings (`MANGOMAS_EVAL__*`).

Named `evaluation` rather than `eval` so the module never shadows the builtin
in a `from mangomas.config import eval` style import."""

from __future__ import annotations

import logging
import math

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


# Evaluation harness defaults.
DEFAULT_EVAL_SCORER: str = "exact_match"


DEFAULT_EVAL_AGENT: str = "chat"


# Default eval target. ``agent`` dispatches a single registered agent (the
# pre-target-indirection behaviour); other built-ins are ``pipeline`` /
# ``fan_out`` / ``echo``. Resolved through ``target_registry``.
DEFAULT_EVAL_TARGET: str = "agent"


# Default dataset source. ``jsonl`` reads a local file (the historical loader);
# other built-ins are ``inline`` / ``langfuse``. Resolved through
# ``dataset_source_registry``.
DEFAULT_EVAL_DATASET_SOURCE: str = "jsonl"


DEFAULT_EVAL_OUTPUT_DIR: str = "eval-output"


DEFAULT_EVAL_PARALLELISM: int = 1


DEFAULT_EVAL_FAIL_FAST: bool = False


# Quality-gate defaults — all OFF so existing runs keep exit code 0 on success.
# ``min_mean_score`` / ``min_pass_rate`` stay ``None`` (no threshold). When a
# threshold is set the CLI exits 3 if the report falls below it.
DEFAULT_EVAL_GATE_ENABLED: bool = False


DEFAULT_EVAL_MIN_MEAN_SCORE: float | None = None


DEFAULT_EVAL_MIN_PASS_RATE: float | None = None


DEFAULT_EVAL_FAIL_ON_ERROR: bool = False


# Cost dimension (Kapoor 2024 / analysis §9.B) — default OFF. USD is not a
# unit score, so these are unbounded-above non-negative floats. The per-row
# scorer budget lives in ``scorer_options["max_cost_usd"]``; the gate field
# below is the only Settings-level cost threshold.
DEFAULT_EVAL_COST_MAX_USD: float | None = None


DEFAULT_EVAL_MAX_MEAN_COST_USD: float | None = None


# Synthetic USD rates for the ``cost_budget`` scorer when a row does not
# supply an explicit ``cost_usd``. Overridable via ``scorer_options``.
DEFAULT_EVAL_COST_USD_PER_1K_INPUT_TOKENS: float = 0.0005


DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_TOKENS: float = 0.0015


DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_CHARS: float = 0.001


# Metadata keys copied onto ``ScoreResult.metadata`` / row metadata. The
# runner averages ``cost_usd`` into ``EvalReport.mean_cost_usd``.
EVAL_COST_USD_METADATA_KEY: str = "cost_usd"


EVAL_COST_INPUT_TOKENS_METADATA_KEY: str = "input_tokens"


EVAL_COST_OUTPUT_TOKENS_METADATA_KEY: str = "output_tokens"


# Regression / baseline gating — default OFF. A baseline is a prior json_file
# report artifact; the gate fails (exit 3) when the current run drops below it
# by more than the configured tolerance, or introduces new row failures.
DEFAULT_EVAL_BASELINE_PATH: str | None = None


DEFAULT_EVAL_MAX_MEAN_SCORE_DROP: float | None = None


DEFAULT_EVAL_MAX_PASS_RATE_DROP: float | None = None


DEFAULT_EVAL_ALLOW_NEW_FAILURES: bool = True


# Result sinks — ``console`` reproduces today's inline stdout summary exactly,
# so the default is behaviour-preserving. Held as a tuple (immutable module
# constant); the field builds a fresh list from it via ``default_factory``.
DEFAULT_EVAL_SINKS: tuple[str, ...] = ("console",)


# Default per-request timeout (seconds) for the optional ``webhook`` sink's
# httpx POST. Overridable per-sink via ``sink_options["webhook"]["timeout_seconds"]``.
DEFAULT_EVAL_WEBHOOK_TIMEOUT_SECONDS: float = 10.0


# Forward-compatible config version marker. Bump when EvalSettings grows a
# field that needs migration; a config declaring a *higher* version than the
# code supports logs a warning rather than crashing.
DEFAULT_EVAL_SCHEMA_VERSION: int = 1


class EvalSettings(BaseModel):
    """Evaluation harness configuration.

    ``dataset_path`` has no safe default — the CLI requires it explicitly so
    that ``mangomas eval`` never runs against an unintended dataset. The
    rest of the fields ship safe defaults so most invocations can rely on
    ``MANGOMAS_EVAL__DATASET_PATH=...`` alone.
    """

    dataset_path: str | None = None
    scorer: str = DEFAULT_EVAL_SCORER
    agent: str = DEFAULT_EVAL_AGENT
    output_dir: str = DEFAULT_EVAL_OUTPUT_DIR
    parallelism: int = DEFAULT_EVAL_PARALLELISM
    fail_fast: bool = DEFAULT_EVAL_FAIL_FAST
    # Free-form per-scorer options (e.g. ``{"threshold": 0.8}``). Forwarded
    # verbatim to the scorer's factory.
    scorer_options: dict[str, object] = Field(default_factory=dict)

    # ── Target indirection ────────────────────────────────────────────────────
    # ``target`` selects what each row dispatches against, resolved through
    # ``target_registry`` (default ``agent`` == today's single-agent dispatch).
    # ``target_options`` carries per-target kwargs keyed by target name, e.g.
    # ``{"pipeline": {"agents": ["planner", "reviewer"]}}``.
    target: str = DEFAULT_EVAL_TARGET
    target_options: dict[str, dict[str, object]] = Field(default_factory=dict)

    # ── Dataset source ────────────────────────────────────────────────────────
    # ``dataset_source`` selects where rows come from, resolved through
    # ``dataset_source_registry`` (default ``jsonl`` == today's file loader).
    # ``dataset_source_options`` carries per-source kwargs keyed by source name,
    # e.g. ``{"inline": {"rows": [...]}}``. For ``jsonl`` the ``--dataset`` flag /
    # ``dataset_path`` still supplies the path.
    dataset_source: str = DEFAULT_EVAL_DATASET_SOURCE
    dataset_source_options: dict[str, dict[str, object]] = Field(default_factory=dict)

    # ── Quality gate (CI) — default OFF ───────────────────────────────────────
    gate_enabled: bool = DEFAULT_EVAL_GATE_ENABLED
    min_mean_score: float | None = DEFAULT_EVAL_MIN_MEAN_SCORE
    min_pass_rate: float | None = DEFAULT_EVAL_MIN_PASS_RATE
    fail_on_error: bool = DEFAULT_EVAL_FAIL_ON_ERROR
    # USD, not a ``[0, 1]`` score. ``None`` keeps the gate cost-blind.
    max_mean_cost_usd: float | None = DEFAULT_EVAL_MAX_MEAN_COST_USD

    # ── Regression / baseline gating (CI) — default OFF ───────────────────────
    baseline_path: str | None = DEFAULT_EVAL_BASELINE_PATH
    max_mean_score_drop: float | None = DEFAULT_EVAL_MAX_MEAN_SCORE_DROP
    max_pass_rate_drop: float | None = DEFAULT_EVAL_MAX_PASS_RATE_DROP
    allow_new_failures: bool = DEFAULT_EVAL_ALLOW_NEW_FAILURES

    # ── Result sinks ──────────────────────────────────────────────────────────
    # Ordered list of sink names resolved through ``sink_registry``. Defaults to
    # ``["console"]`` (== today's inline output). ``sink_options`` carries
    # per-sink kwargs keyed by sink name, e.g. ``{"json_file": {"path": "..."}}``.
    sinks: list[str] = Field(default_factory=lambda: list(DEFAULT_EVAL_SINKS))
    sink_options: dict[str, dict[str, object]] = Field(default_factory=dict)

    # ── Forward-compatible schema version ─────────────────────────────────────
    schema_version: int = DEFAULT_EVAL_SCHEMA_VERSION

    @model_validator(mode="after")
    def _validate_eval(self) -> EvalSettings:
        """Bound gate thresholds to ``[0, 1]`` and tolerate future schema versions.

        Thresholds are normalised scores, so a value outside ``[0, 1]`` is a
        configuration error (fail fast at construction). A ``schema_version``
        ahead of what this build supports is *not* fatal — it is logged so a
        newer config can be read by older code without crashing (forward-compat).
        """
        for label, value in (
            ("min_mean_score", self.min_mean_score),
            ("min_pass_rate", self.min_pass_rate),
            ("max_mean_score_drop", self.max_mean_score_drop),
            ("max_pass_rate_drop", self.max_pass_rate_drop),
        ):
            if value is not None and not 0.0 <= value <= 1.0:
                raise ValueError(f"eval.{label} must be in [0.0, 1.0]; got {value}")
        if self.max_mean_cost_usd is not None and (
            not math.isfinite(self.max_mean_cost_usd) or self.max_mean_cost_usd < 0.0
        ):
            raise ValueError(
                "eval.max_mean_cost_usd must be a finite number >= 0.0; "
                f"got {self.max_mean_cost_usd}"
            )
        if self.schema_version > DEFAULT_EVAL_SCHEMA_VERSION:
            logger.warning(
                "Eval config declares a future schema_version; reading with current code",
                extra={
                    "event": "eval_config_future_version",
                    "declared": self.schema_version,
                    "supported": DEFAULT_EVAL_SCHEMA_VERSION,
                },
            )
        return self
