"""Offline evaluation harness for Mango-Mas agents.

Public surface — keep stable; downstream consumers may import from here.
"""

from __future__ import annotations

from mangomas.eval.dataset import DatasetRow, load_jsonl
from mangomas.eval.protocol import (
    Scorer,
    ScorerContext,
    ScoreResult,
)
from mangomas.eval.registry import ScorerFactory, scorer_registry
from mangomas.eval.runner import EvalReport, EvalRowResult, EvalRunner

__all__ = [
    "DatasetRow",
    "EvalReport",
    "EvalRowResult",
    "EvalRunner",
    "ScoreResult",
    "Scorer",
    "ScorerContext",
    "ScorerFactory",
    "load_jsonl",
    "scorer_registry",
]
