"""json_field comparisons: parse a JSON object and test one dotted path.

The closures are total. Unparseable content, a non-object, or a path that
does not resolve is "not accepted", never an exception.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from mangomas.core.structured import parse_llm_json_object
from mangomas.errors import ConfigError, LLMBadResponse
from mangomas.workflow.predicate._spec import (
    _AT_LEAST,
    _AT_MOST,
    _COMPARISON_FIELDS,
    _EQUALS,
    _KIND_JSON_FIELD,
    PredicateSpec,
)

_Comparison = Callable[[Any], bool]


def _as_number(found: Any) -> float | None:
    """Return *found* as a float, or ``None`` when it is not a number.

    ``bool`` is rejected first: ``True >= 0.5`` is otherwise silently true.
    """
    if isinstance(found, bool):
        return None
    if isinstance(found, int | float):
        try:
            return float(found)
        except OverflowError:
            return None
    return None


def _build_equals(expected: Any) -> _Comparison:
    def _equals(found: Any) -> bool:
        # ``True == 1`` in Python. Compare bool-ness before equality, both ways.
        if (type(found) is bool) is not (type(expected) is bool):
            return False
        return bool(found == expected)

    return _equals


def _build_at_least(threshold: Any) -> _Comparison:
    def _at_least(found: Any) -> bool:
        number = _as_number(found)
        return number is not None and number >= threshold

    return _at_least


def _build_at_most(threshold: Any) -> _Comparison:
    def _at_most(found: Any) -> bool:
        number = _as_number(found)
        return number is not None and number <= threshold

    return _at_most


_COMPARISON_BUILDERS: dict[str, Callable[[Any], _Comparison]] = {
    _EQUALS: _build_equals,
    _AT_LEAST: _build_at_least,
    _AT_MOST: _build_at_most,
}


def _build_comparison(spec: PredicateSpec) -> _Comparison:
    """Select the single comparison *spec* names, or raise ``ConfigError``."""
    given = [name for name in _COMPARISON_FIELDS if getattr(spec, name) is not None]
    if len(given) != 1:
        raise ConfigError(
            f"predicate kind {_KIND_JSON_FIELD!r} requires exactly one of "
            f"{list(_COMPARISON_FIELDS)}; got {given}"
        )
    name = given[0]
    return _COMPARISON_BUILDERS[name](getattr(spec, name))


def _parse_object(content: str) -> dict[str, Any] | None:
    """Strict whole-text JSON-object parse; ``None`` when *content* is not one."""
    try:
        return parse_llm_json_object(content)
    except (LLMBadResponse, ValueError, RecursionError):
        return None


def _resolve(data: Mapping[str, Any], segments: tuple[str, ...]) -> tuple[bool, Any]:
    """Walk a dotted path over mappings. ``(False, None)`` when it runs off."""
    current: Any = data
    for segment in segments:
        if not isinstance(current, Mapping) or segment not in current:
            return False, None
        current = current[segment]
    return True, current
