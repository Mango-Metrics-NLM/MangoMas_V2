"""`json_field` scorer — structured acceptance over a parsed field (spec-0045 B5).

Ports the ADR-0034 predicate semantics (`workflow/predicate/_json_field.py`)
into a scorer so planner / reviewer JSON is graded on a *parsed* value rather
than a substring: dotted path over mappings, exactly one comparison, strict
whole-text parse via :func:`~mangomas.core.structured.parse_llm_json_object`
so it agrees with ``VALIDATE_OUTPUT``, booleans excluded from numeric
comparison, and an unparseable or unresolvable prediction is a failing row
(``score 0.0``), never an exception. The two packages are kept independent by
the import-linter contract, so the semantics are re-implemented here rather
than imported; `tests/eval/test_json_field_scorer.py` pins them against the
predicate's own cases.

One additive comparison beyond ADR-0034: ``min_length`` on a list or string
(``ExecutionPlan.steps`` is a list, and "at least one step" is the planner's
most basic contract). The path walks **mappings only** — a list index is not a
segment — which is also why argument-level checks over ``tool_calls`` (a list)
are not expressed with this scorer.

The check comes from the scorer's options, or per row from
``metadata["json_field"]`` (the row wins), so one dataset can hold rows with
different expectations.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Final

from mangomas.core.structured import parse_llm_json_object
from mangomas.errors import ConfigError, LLMBadResponse
from mangomas.eval.protocol import Scorer, ScorerContext, ScoreResult
from mangomas.eval.registry import scorer_registry

logger = logging.getLogger(__name__)

ROW_METADATA_KEY: Final[str] = "json_field"
_PATH: Final[str] = "path"
_EQUALS: Final[str] = "equals"
_AT_LEAST: Final[str] = "at_least"
_AT_MOST: Final[str] = "at_most"
_MIN_LENGTH: Final[str] = "min_length"
COMPARISONS: Final[tuple[str, ...]] = (_EQUALS, _AT_LEAST, _AT_MOST, _MIN_LENGTH)


def _as_number(found: Any) -> float | None:
    """*found* as a float, or ``None``; ``bool`` is rejected (``True >= 0.5``)."""
    if isinstance(found, bool) or not isinstance(found, int | float):
        return None
    try:
        return float(found)
    except OverflowError:
        return None


def _resolve(data: Mapping[str, Any], segments: tuple[str, ...]) -> tuple[bool, Any]:
    """Walk a dotted path over mappings. ``(False, None)`` when it runs off."""
    current: Any = data
    for segment in segments:
        if not isinstance(current, Mapping) or segment not in current:
            return False, None
        current = current[segment]
    return True, current


def _compare(comparison: str, operand: Any, found: Any) -> bool:
    if comparison == _EQUALS:
        if (type(found) is bool) is not (type(operand) is bool):
            return False
        return bool(found == operand)
    if comparison == _MIN_LENGTH:
        return isinstance(found, list | str) and len(found) >= operand
    number = _as_number(found)
    if number is None:
        return False
    return bool(number >= operand) if comparison == _AT_LEAST else bool(number <= operand)


def _validate_check(check: Mapping[str, Any], *, owner: str) -> tuple[str, str, Any]:
    """Return ``(path, comparison, operand)`` or raise ``ConfigError``."""
    path = check.get(_PATH)
    if not isinstance(path, str) or not path.strip():
        raise ConfigError(f"{owner}: 'path' must be a non-empty dotted string")
    given = [name for name in COMPARISONS if name in check]
    if len(given) != 1:
        raise ConfigError(f"{owner}: exactly one of {list(COMPARISONS)} is required; got {given}")
    comparison = given[0]
    operand = check[comparison]
    if comparison in (_AT_LEAST, _AT_MOST) and _as_number(operand) is None:
        raise ConfigError(f"{owner}: {comparison!r} needs a numeric threshold; got {operand!r}")
    if comparison == _MIN_LENGTH and (
        isinstance(operand, bool) or not isinstance(operand, int) or operand < 0
    ):
        raise ConfigError(f"{owner}: {_MIN_LENGTH!r} needs a non-negative int; got {operand!r}")
    return path, comparison, operand


class JsonFieldScorer:
    """Pass iff one parsed field of the prediction satisfies one comparison."""

    name = "json_field"

    def __init__(self, *, check: Mapping[str, Any] | None = None) -> None:
        self._default = _validate_check(check, owner=self.name) if check is not None else None

    def _check_for(self, context: ScorerContext | None) -> tuple[str, str, Any]:
        row_check = None if context is None else context.row_metadata.get(ROW_METADATA_KEY)
        if isinstance(row_check, Mapping):
            return _validate_check(row_check, owner=f"{self.name} row metadata")
        if self._default is None:
            raise ConfigError(
                f"{self.name}: no check configured — set scorer options or row "
                f"metadata[{ROW_METADATA_KEY!r}] with 'path' and one comparison"
            )
        return self._default

    async def score(
        self,
        prediction: str,
        expected: str,  # noqa: ARG002 — the check is the expectation
        *,
        context: ScorerContext | None = None,
    ) -> ScoreResult:
        path, comparison, operand = self._check_for(context)
        metadata: dict[str, Any] = {
            "scorer": self.name,
            "path": path,
            "comparison": comparison,
            "operand": operand,
        }
        try:
            parsed = parse_llm_json_object(prediction)
        except (LLMBadResponse, ValueError, RecursionError):
            metadata["error"] = "not_json_object"
            return ScoreResult(score=0.0, passed=False, metadata=metadata)
        resolved, found = _resolve(parsed, tuple(path.split(".")))
        if not resolved:
            metadata["error"] = "path_not_found"
            return ScoreResult(score=0.0, passed=False, metadata=metadata)
        metadata["found"] = found
        accepted = _compare(comparison, operand, found)
        return ScoreResult(score=1.0 if accepted else 0.0, passed=accepted, metadata=metadata)


def _json_field_factory(options: dict[str, Any]) -> Scorer:
    return JsonFieldScorer(check=options or None)


scorer_registry.register("json_field", _json_field_factory)
