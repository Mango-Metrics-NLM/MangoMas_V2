"""Declarative acceptance predicates compiled to a synchronous ``AcceptanceFn``."""

from __future__ import annotations

from ._client import (
    FIELD_PATH_SEPARATOR,
    KIND_JSON_FIELD,
    TEXT_MATCH_KINDS,
    PredicateSpec,
    compile_predicate,
)

__all__ = [
    "FIELD_PATH_SEPARATOR",
    "KIND_JSON_FIELD",
    "TEXT_MATCH_KINDS",
    "PredicateSpec",
    "compile_predicate",
]
