"""Declarative multi-agent workflow graphs (opt-in; spec 0005 / ADR-0011).

A :class:`WorkflowGraph` is compiled down to the existing ``Orchestrator``
dispatch primitives — no new execution engine, no ``Agent``-protocol change, and
default-OFF. Importing this package fully wires the node registry (via ``.nodes``),
so any caller of :func:`execute_workflow` that does ``import mangomas.workflow``
has a seeded registry — no separate side-effect import is required.
"""

from __future__ import annotations

# Seed the node registry: each node module self-registers on import. Every
# intra-package import uses the submodule path (never ``from mangomas.workflow
# import ...``), so importing ``nodes`` here never re-enters this ``__init__`` —
# the seeding is therefore order-independent and cycle-free.
from mangomas.workflow import nodes  # noqa: F401  (imported for registration side effect)
from mangomas.workflow.executor import NodeExecutor, execute_workflow
from mangomas.workflow.graph import (
    AgentNode,
    BranchCase,
    BranchNode,
    FanOutNode,
    LoopNode,
    SequenceNode,
    WorkflowGraph,
    WorkflowNode,
)
from mangomas.workflow.loader import (
    SUPPORTED_SCHEMA_VERSIONS,
    load_workflow,
    resolve_workflow_source,
)
from mangomas.workflow.predicate import PredicateSpec, compile_predicate
from mangomas.workflow.registry import node_registry, resolve_executor

__all__ = [
    "SUPPORTED_SCHEMA_VERSIONS",
    "AgentNode",
    "BranchCase",
    "BranchNode",
    "FanOutNode",
    "LoopNode",
    "NodeExecutor",
    "PredicateSpec",
    "SequenceNode",
    "WorkflowGraph",
    "WorkflowNode",
    "compile_predicate",
    "execute_workflow",
    "load_workflow",
    "node_registry",
    "resolve_executor",
    "resolve_workflow_source",
]
