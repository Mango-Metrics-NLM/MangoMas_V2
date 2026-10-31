"""Compile a :class:`PredicateSpec` into a synchronous ``AcceptanceFn``.

The spec and its validator live in ``_spec``. ``json_field`` comparisons live
in ``_json_field``. This module is the compiler and the log seam: both the
compile event and the per-step evaluate event are emitted from here, under
this module's logger name.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Final

from mangomas.errors import ConfigError
from mangomas.utils.regex_flags import resolve_regex_flags
from mangomas.workflow.predicate._json_field import (
    _build_comparison,
    _parse_object,
    _resolve,
)
from mangomas.workflow.predicate._spec import (
    _FIELD,
    _FIELD_PATH_SEPARATOR,
    _KIND_CONTAINS,
    _KIND_JSON_FIELD,
    _VALUE,
    FIELD_PATH_SEPARATOR,
    KIND_JSON_FIELD,
    TEXT_MATCH_KINDS,
    PredicateSpec,
)

__all__ = [
    "FIELD_PATH_SEPARATOR",
    "KIND_JSON_FIELD",
    "TEXT_MATCH_KINDS",
    "PredicateSpec",
    "compile_predicate",
]

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import AgentResponse
    from mangomas.core.loop import AcceptanceFn

logger = logging.getLogger(__name__)

_COMPILE_EVENT: Final[str] = "workflow_predicate_compiled"
_EVALUATE_EVENT: Final[str] = "workflow_predicate_evaluated"


def _required_value(spec: PredicateSpec) -> str:
    """Return the text needle, or raise ``ConfigError``."""
    if not spec.value:
        raise ConfigError(f"predicate kind {spec.kind!r} requires a non-empty {_VALUE!r}")
    return spec.value


def _compile_contains(spec: PredicateSpec) -> AcceptanceFn:
    case_sensitive = spec.case_sensitive
    raw = _required_value(spec)
    needle = raw if case_sensitive else raw.lower()

    def _contains(response: AgentResponse) -> bool:
        haystack = response.content if case_sensitive else response.content.lower()
        return needle in haystack

    return _contains


def _compile_regex(spec: PredicateSpec) -> AcceptanceFn:
    flags = resolve_regex_flags(spec.flags)
    raw = _required_value(spec)
    try:
        pattern = re.compile(raw, flags)
    except re.error as exc:
        raise ConfigError(f"invalid regex pattern {raw!r}: {exc}") from exc

    def _regex(response: AgentResponse) -> bool:
        return pattern.search(response.content) is not None

    return _regex


def _compile_json_field(spec: PredicateSpec) -> AcceptanceFn:
    path = spec.field
    if not path:
        raise ConfigError(f"predicate kind {_KIND_JSON_FIELD!r} requires a non-empty {_FIELD!r}")
    compare = _build_comparison(spec)
    segments = tuple(path.split(_FIELD_PATH_SEPARATOR))

    def _json_field(response: AgentResponse) -> bool:
        data = _parse_object(response.content)
        resolved, found = (False, None) if data is None else _resolve(data, segments)
        verdict = resolved and compare(found)
        logger.debug(
            "workflow json_field predicate evaluated",
            extra={
                "event": _EVALUATE_EVENT,
                "predicate_kind": _KIND_JSON_FIELD,
                "field_path": path,
                "parsed": data is not None,
                "resolved": resolved,
                "verdict": verdict,
            },
        )
        return verdict

    return _json_field


def compile_predicate(spec: PredicateSpec) -> AcceptanceFn:
    """Compile *spec* into a pure, synchronous ``AcceptanceFn``.

    Raises :class:`~mangomas.errors.ConfigError` on an invalid regex flag, an
    uncompilable pattern, or a ``json_field`` spec whose comparison is not
    exactly right. The returned closure is total: no response content makes
    it raise.
    """
    logger.debug(
        "compiling workflow acceptance predicate",
        extra={"event": _COMPILE_EVENT, "predicate_kind": spec.kind},
    )
    if spec.kind == _KIND_CONTAINS:
        return _compile_contains(spec)
    if spec.kind == _KIND_JSON_FIELD:
        return _compile_json_field(spec)
    return _compile_regex(spec)
