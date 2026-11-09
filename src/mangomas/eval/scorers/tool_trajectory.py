"""Tool-trajectory scorer (spec-0045 B4).

Grades *which tools ran*, read from the trace ``ToolAgent`` writes into
``response_metadata["tool_calls"]``, against the row's
``metadata["expected_tools"]``. Four observable facts, reported separately
rather than blended into one similarity:

* ``recall`` — fraction of expected tools that were called (``1.0`` when the
  row expects none);
* ``missing_tools`` / ``extra_tools`` — the two sides of the set difference;
* ``steps`` — the LLM-call count ``ToolAgent`` reports, checked against the
  optional ``max_steps`` budget.

``passed`` requires full recall, no unexpected tool unless ``allow_extra``,
and the step budget. ``score`` is the recall when those hold and ``0.0``
otherwise, so a run that called the right tools *and* an unexpected one does
not read as mostly right. Arguments are not graded here: the trace carries
digests by default (``MANGOMAS_AGENTS__<NAME>__TOOL_TRACE_ARGUMENTS``), and
argument-level assertions are a separate, later concern.

A refused call never reaches this scorer — the agent raises and the row's
``expected_error`` contract decides — which is why there is no "violations"
count: the harness records the refusal as the row's outcome itself.
"""

from __future__ import annotations

import logging
from typing import Any

from mangomas.errors import ConfigError
from mangomas.eval.protocol import Scorer, ScorerContext, ScoreResult
from mangomas.eval.registry import scorer_registry

logger = logging.getLogger(__name__)

EXPECTED_TOOLS_METADATA_KEY: str = "expected_tools"
TOOL_CALLS_METADATA_KEY: str = "tool_calls"
TOOL_STEPS_METADATA_KEY: str = "tool_steps"


def _names(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict) and isinstance(item.get("tool"), str):
            names.append(item["tool"])
    return names


class ToolTrajectoryScorer:
    """Did the agent call the expected tools, only those, within budget?"""

    name = "tool_trajectory"

    def __init__(self, *, allow_extra: bool = False, max_steps: int | None = None) -> None:
        if max_steps is not None and max_steps < 1:
            raise ConfigError(f"{self.name}: max_steps must be >= 1 when set; got {max_steps}")
        self._allow_extra = allow_extra
        self._max_steps = max_steps

    async def score(
        self,
        prediction: str,  # noqa: ARG002 — the trajectory, not the text, is graded
        expected: str,  # noqa: ARG002
        *,
        context: ScorerContext | None = None,
    ) -> ScoreResult:
        row_metadata = dict(context.row_metadata) if context is not None else {}
        response_metadata = dict(context.response_metadata) if context is not None else {}
        expected_tools = _names(row_metadata.get(EXPECTED_TOOLS_METADATA_KEY))
        observed = _names(response_metadata.get(TOOL_CALLS_METADATA_KEY))
        expected_set, observed_set = set(expected_tools), set(observed)
        missing = sorted(expected_set - observed_set)
        extra = sorted(observed_set - expected_set)
        recall = 1.0 if not expected_set else 1.0 - len(missing) / len(expected_set)
        steps_raw = response_metadata.get(TOOL_STEPS_METADATA_KEY)
        steps = (
            steps_raw if isinstance(steps_raw, int) and not isinstance(steps_raw, bool) else None
        )
        within_budget = self._max_steps is None or (steps is not None and steps <= self._max_steps)
        passed = recall == 1.0 and (self._allow_extra or not extra) and within_budget
        metadata: dict[str, Any] = {
            "scorer": self.name,
            "recall": recall,
            "expected_tools": expected_tools,
            "observed_tools": observed,
            "missing_tools": missing,
            "extra_tools": extra,
            "steps": steps,
            "max_steps": self._max_steps,
            "allow_extra": self._allow_extra,
        }
        logger.debug("Tool trajectory scored", extra={"event": "eval_tool_trajectory", **metadata})
        return ScoreResult(score=recall if passed else 0.0, passed=passed, metadata=metadata)


def _tool_trajectory_factory(options: dict[str, Any]) -> Scorer:
    allow_extra = options.get("allow_extra", False)
    if not isinstance(allow_extra, bool):
        raise ConfigError(f"tool_trajectory: allow_extra must be a bool; got {allow_extra!r}")
    max_steps = options.get("max_steps")
    if max_steps is not None and (isinstance(max_steps, bool) or not isinstance(max_steps, int)):
        raise ConfigError(f"tool_trajectory: max_steps must be an int; got {max_steps!r}")
    return ToolTrajectoryScorer(allow_extra=allow_extra, max_steps=max_steps)


scorer_registry.register("tool_trajectory", _tool_trajectory_factory)
