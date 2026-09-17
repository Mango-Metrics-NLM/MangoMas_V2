"""Declarative acceptance predicates compiled to a synchronous ``AcceptanceFn``."""
from __future__ import annotations

from ._client import PredicateSpec, compile_predicate

__all__ = ["PredicateSpec", "compile_predicate"]
