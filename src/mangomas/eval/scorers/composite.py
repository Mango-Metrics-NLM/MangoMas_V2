"""`composite` scorer — several scorers per row, recorded separately (spec-0045 B5).

The planner dataset needs `json_keys` *and* `json_field`; the safety dataset
needs `tool_trajectory` and a text check. Rather than one blended number,
every child's result is kept first-class under ``metadata["scores"]`` and the
row's own verdict is the strictest reading of them: ``score = min`` and
``passed = all``. Weights and means were considered and rejected (ADR-0041):
they hide which criterion failed and invite a single blended threshold.

Children are resolved through :data:`~mangomas.eval.registry.scorer_registry`
at construction, so a misnamed child is a configuration error before any row
runs, and a child may itself be any registered scorer — including a plugin.
"""

from __future__ import annotations

import logging
from typing import Any

from mangomas.config import EVAL_COST_USD_METADATA_KEY
from mangomas.errors import ConfigError
from mangomas.eval._options import require_list
from mangomas.eval.protocol import Scorer, ScorerContext, ScoreResult
from mangomas.eval.registry import scorer_registry

logger = logging.getLogger(__name__)

SCORES_METADATA_KEY = "scores"


class CompositeScorer:
    """``min`` of the children's scores; passes only when every child passes."""

    name = "composite"

    def __init__(self, children: list[Scorer]) -> None:
        if not children:
            raise ConfigError(f"{self.name}: at least one child scorer is required")
        names = [child.name for child in children]
        if len(set(names)) != len(names):
            raise ConfigError(f"{self.name}: child scorer names must be unique; got {names}")
        self._children = list(children)

    @property
    def children(self) -> tuple[Scorer, ...]:
        return tuple(self._children)

    async def score(
        self,
        prediction: str,
        expected: str,
        *,
        context: ScorerContext | None = None,
    ) -> ScoreResult:
        scores: dict[str, dict[str, Any]] = {}
        for child in self._children:
            result = await child.score(prediction, expected, context=context)
            scores[child.name] = {
                "score": result.score,
                "passed": result.passed,
                "metadata": dict(result.metadata),
            }
        passed = all(entry["passed"] for entry in scores.values())
        score = min(entry["score"] for entry in scores.values())
        failed = [name for name, entry in scores.items() if not entry["passed"]]
        metadata: dict[str, Any] = {
            "scorer": self.name,
            SCORES_METADATA_KEY: scores,
            "failed": failed,
        }
        metadata.update(_cost_of(scores))
        logger.debug(
            "Composite scored",
            extra={"event": "eval_composite", "children": list(scores), "failed": failed},
        )
        return ScoreResult(score=score, passed=passed, metadata=metadata)


def _cost_of(scores: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Lift the first child's ``cost_usd`` (and its ``source``) to the row's top level.

    The runner's ``mean_cost_usd`` and the ``max_mean_cost_usd`` gate read only
    the row's top-level ``cost_usd``; left nested under ``scores`` a
    ``cost_budget`` child would measure every row while the gate silently
    reported cost as unavailable and skipped (Copilot review on PR #124).
    Children never disagree in practice — one ``cost_budget`` per composite —
    so the first reporter wins and the others stay visible under ``scores``.
    """
    for entry in scores.values():
        child_metadata = entry["metadata"]
        if EVAL_COST_USD_METADATA_KEY in child_metadata:
            lifted: dict[str, Any] = {
                EVAL_COST_USD_METADATA_KEY: child_metadata[EVAL_COST_USD_METADATA_KEY]
            }
            if "source" in child_metadata:
                lifted["source"] = child_metadata["source"]
            return lifted
    return {}


def build_composite(specs: list[Any]) -> CompositeScorer:
    """Build children from ``[{"name": ..., "options": {...}}, ...]`` (or bare names)."""
    children: list[Scorer] = []
    for spec in specs:
        options: dict[str, Any]
        if isinstance(spec, str):
            name, options = spec, {}
        elif isinstance(spec, dict) and isinstance(spec.get("name"), str):
            name = spec["name"]
            options = spec.get("options") or {}
            if not isinstance(options, dict):
                raise ConfigError(f"composite: child {name!r} 'options' must be an object")
        else:
            raise ConfigError(
                f"composite: each child must be a name or {{name, options}}; got {spec!r}"
            )
        if name == CompositeScorer.name:
            raise ConfigError("composite: a composite may not nest another composite")
        children.append(scorer_registry.get(name)(dict(options)))
    return CompositeScorer(children)


def _composite_factory(options: dict[str, Any]) -> Scorer:
    return build_composite(require_list(options, "scorers", owner="composite"))


scorer_registry.register("composite", _composite_factory)
