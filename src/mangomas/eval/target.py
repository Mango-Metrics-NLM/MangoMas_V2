"""Target protocol — what an eval run dispatches each row against.

A :class:`Target` decouples the :class:`~mangomas.eval.runner.EvalRunner` from a
single registered agent: a run can target a registered agent (the default,
preserving today's behaviour), a multi-agent pipeline / fan-out topology, or a
deterministic ``echo`` baseline (used for regression comparison and tests).
Targets are resolved by name through
:data:`~mangomas.eval.target_registry.target_registry` exactly as scorers and
sinks are.

``run`` is async (real targets perform I/O through the orchestrator) and returns
a :class:`TargetOutcome`: the prediction string the scorer grades **plus** the
response metadata the dispatch produced (loop counters, tool calls, measured
token usage). Until spec-0045 the contract returned a bare ``str`` and that
metadata was discarded at this seam — the structural reason eval cost was
"declared, never measured" (D15). The orchestrator is supplied per call — it is
only available at runtime inside the runner — mirroring how
:meth:`Scorer.score` receives its :class:`ScorerContext`.

A third-party target written against the ``str`` contract still works: the
runner wraps a ``str`` return through :func:`coerce_outcome` with a one-time
WARNING, and ``EvalSettings.schema_version`` moved ``1 → 2`` to mark the change.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from mangomas.config import (
    EVAL_COST_INPUT_TOKENS_METADATA_KEY,
    EVAL_COST_OUTPUT_TOKENS_METADATA_KEY,
    EVAL_USAGE_LLM_CALLS_KEY,
    EVAL_USAGE_METADATA_KEY,
)

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import AgentRequest, Orchestrator
    from mangomas.utils.llm_usage import UsageTotals

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TargetOutcome:
    """What a target produced for one row: the graded text and its metadata."""

    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Target(Protocol):
    """A named, dispatchable evaluation target."""

    name: str

    async def run(self, request: AgentRequest, *, orch: Orchestrator) -> TargetOutcome:
        """Produce the graded content and response metadata for *request* using *orch*."""
        ...


def with_usage(metadata: Mapping[str, Any], totals: UsageTotals) -> dict[str, Any]:
    """Copy *metadata* and attach the measured usage block (spec-0045 B3).

    Written even when ``llm_calls`` is ``0``: a deterministic target and a
    silent provider look different to a reader only if the count is there.
    ``cost_budget`` treats a zero count as "not measured" (see
    :func:`~mangomas.eval.scorers.cost_budget.measured_usage`).
    """
    merged = dict(metadata)
    merged[EVAL_USAGE_METADATA_KEY] = totals.as_metadata(
        input_key=EVAL_COST_INPUT_TOKENS_METADATA_KEY,
        output_key=EVAL_COST_OUTPUT_TOKENS_METADATA_KEY,
        calls_key=EVAL_USAGE_LLM_CALLS_KEY,
    )
    return merged


def coerce_outcome(value: object, *, target_name: str) -> TargetOutcome:
    """Accept the pre-spec-0045 ``str`` return from a third-party target.

    A plugin registered through the ``mangomas.eval.targets`` entry-point group
    may predate :class:`TargetOutcome`. Its string is still a valid prediction;
    it simply carries no metadata, which is exactly what the old seam did. Any
    other type is a contract violation and raises, so a target returning
    ``None`` is a loud error rather than a silently empty prediction.
    """
    if isinstance(value, TargetOutcome):
        return value
    if isinstance(value, str):
        logger.warning(
            "Eval target %r returned str; wrap it in TargetOutcome (spec-0045)",
            target_name,
            extra={"event": "eval_target_legacy_str", "target": target_name},
        )
        return TargetOutcome(content=value)
    raise TypeError(
        f"eval target {target_name!r} returned {type(value).__name__}; expected TargetOutcome"
    )
