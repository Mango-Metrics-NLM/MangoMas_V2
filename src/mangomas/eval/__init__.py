"""Offline evaluation harness for Mango-Mas agents.

Public surface — keep stable; downstream consumers may import from here.
"""

from __future__ import annotations

from mangomas.eval.dataset import DatasetRow, load_jsonl
from mangomas.eval.dataset_source import (
    DatasetSource,
    DatasetSourceFactory,
    dataset_source_registry,
)
from mangomas.eval.discovery import (
    discover_dataset_sources,
    discover_scorers,
    discover_sinks,
    discover_targets,
    ensure_eval_plugins,
)
from mangomas.eval.gate import GateResult, evaluate_gate
from mangomas.eval.protocol import (
    Scorer,
    ScorerContext,
    ScoreResult,
)
from mangomas.eval.registry import ScorerFactory, scorer_registry
from mangomas.eval.runner import EvalReport, EvalRowResult, EvalRunner
from mangomas.eval.sink import Sink
from mangomas.eval.sink_registry import SinkFactory, sink_registry
from mangomas.eval.target import Target
from mangomas.eval.target_registry import TargetFactory, target_registry

__all__ = [
    "DatasetRow",
    "DatasetSource",
    "DatasetSourceFactory",
    "EvalReport",
    "EvalRowResult",
    "EvalRunner",
    "GateResult",
    "ScoreResult",
    "Scorer",
    "ScorerContext",
    "ScorerFactory",
    "Sink",
    "SinkFactory",
    "Target",
    "TargetFactory",
    "dataset_source_registry",
    "discover_dataset_sources",
    "discover_scorers",
    "discover_sinks",
    "discover_targets",
    "ensure_eval_plugins",
    "evaluate_gate",
    "load_jsonl",
    "scorer_registry",
    "sink_registry",
    "target_registry",
]
