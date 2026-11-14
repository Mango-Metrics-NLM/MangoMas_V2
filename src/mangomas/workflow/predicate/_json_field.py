"""json_field comparisons: parse a JSON object and test one dotted path.

The closures are total. Unparseable content, a non-object, or a path that
does not resolve is "not accepted", never an exception.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mangomas.core.structured import parse_llm_json_object
from mangomas.errors import ConfigError, LLMBadResponse
from mangomas.utils.json_path import (
    finite_number,
    resolve_dotted_path,
    strict_json_equals,
)
from mangomas.workflow.predicate._spec import (
    _AT_LEAST,
    _AT_MOST,
    _COMPARISON_FIELDS,
    _EQUALS,
    _KIND_JSON_FIELD,
    PredicateSpec,
)

_Comparison = Callable[[Any], bool]


def _build_equals(expected: Any) -> _Comparison:
    def _equals(found: Any) -> bool:
        return strict_json_equals(found, expected)

    return _equals


def _build_at_least(threshold: Any) -> _Comparison:
    def _at_least(found: Any) -> bool:
        number = finite_number(found)
        return number is not None and number >= threshold

    return _at_least


def _build_at_most(threshold: Any) -> _Comparison:
    def _at_most(found: Any) -> bool:
        number = finite_number(found)
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


# Retained aliases so workflow.predicate and regression tests share the helpers.
_as_number = finite_number
_resolve = resolve_dotted_path
