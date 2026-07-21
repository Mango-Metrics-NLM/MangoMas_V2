"""Declarative acceptance predicates compiled to a synchronous ``AcceptanceFn``.

A :class:`PredicateSpec` is pure data (JSON-friendly) describing how to decide
whether an agent response "accepts" — used by the ``loop`` workflow node to build
the ``acceptance_fn`` that :meth:`mangomas.core.Orchestrator.dispatch` consumes.
The predicate is compiled once into a plain **synchronous** closure; it must stay
sync (no I/O, no ``await``) because ``AcceptanceFn`` is a
``Callable[[AgentResponse], bool]`` the dispatch loop calls inline.

The regex-flag map mirrors :mod:`mangomas.eval.scorers.regex_match` but is copied
here deliberately: the ``workflow`` package is a pure-domain sibling of ``eval``
and must not import ``eval`` internals.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from mangomas.errors import ConfigError

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import AgentResponse
    from mangomas.core.loop import AcceptanceFn

# Copied (not imported) from eval/scorers/regex_match.py so ``workflow`` carries
# no dependency on the ``eval`` package.
_FLAG_BY_NAME: dict[str, re.RegexFlag] = {
    "ignorecase": re.IGNORECASE,
    "multiline": re.MULTILINE,
    "dotall": re.DOTALL,
}


class PredicateSpec(BaseModel):
    """Declarative acceptance predicate (the ``loop`` node ``accept`` field).

    ``case_sensitive`` applies to ``contains`` only (substring match is
    case-insensitive unless set ``True``); ``flags`` applies to ``regex`` only
    (any of ``ignorecase`` / ``multiline`` / ``dotall``, OR-combined).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["contains", "regex"]
    value: str = Field(min_length=1)
    case_sensitive: bool = False
    flags: list[str] = Field(default_factory=list)


def _resolve_flags(names: list[str]) -> int:
    flags = 0
    for name in names:
        flag = _FLAG_BY_NAME.get(name.lower())
        if flag is None:
            raise ConfigError(f"unknown regex flag {name!r}; valid: {sorted(_FLAG_BY_NAME)}")
        flags |= flag
    return flags


def compile_predicate(spec: PredicateSpec) -> AcceptanceFn:
    """Compile *spec* into a pure, synchronous ``AcceptanceFn``.

    Raises :class:`~mangomas.errors.ConfigError` on an invalid regex flag or an
    uncompilable pattern — at compile time, not per call.
    """
    if spec.kind == "contains":
        case_sensitive = spec.case_sensitive
        needle = spec.value if case_sensitive else spec.value.lower()

        def _contains(response: AgentResponse) -> bool:
            haystack = response.content if case_sensitive else response.content.lower()
            return needle in haystack

        return _contains

    flags = _resolve_flags(spec.flags)
    try:
        pattern = re.compile(spec.value, flags)
    except re.error as exc:
        raise ConfigError(f"invalid regex pattern {spec.value!r}: {exc}") from exc

    def _regex(response: AgentResponse) -> bool:
        return pattern.search(response.content) is not None

    return _regex
