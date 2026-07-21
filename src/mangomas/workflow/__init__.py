"""Declarative multi-agent workflow graphs (spec 0005, ADR-0007).

Opt-in and default-OFF: nothing here runs unless a workflow is defined and
enabled via ``MANGOMAS_WORKFLOW__*``. The layer compiles a declarative graph
onto the existing orchestrator primitives (``dispatch`` / ``dispatch_fan_out`` /
the acceptance loop) — it adds no new execution engine and no core changes.

Typical use::

    from mangomas.composition import build_orchestrator
    from mangomas.workflow import WorkflowRunner, graph_from_settings

    orch = build_orchestrator(settings)
    graph = graph_from_settings(settings.workflow)  # or parse_graph({...})
    if graph is not None:
        response = await WorkflowRunner(graph).run(request, orch=orch)
"""

from __future__ import annotations

from mangomas.workflow.loader import (
    graph_from_settings,
    load_graph_file,
    load_graph_json,
    parse_graph,
)
from mangomas.workflow.models import (
    DEFAULT_WORKFLOW_JOIN,
    DEFAULT_WORKFLOW_NAME,
    JOIN_CONCAT,
    JOIN_FIRST,
    WorkflowGraph,
    WorkflowNode,
)
from mangomas.workflow.runner import WorkflowRunner

__all__ = [
    "DEFAULT_WORKFLOW_JOIN",
    "DEFAULT_WORKFLOW_NAME",
    "JOIN_CONCAT",
    "JOIN_FIRST",
    "WorkflowGraph",
    "WorkflowNode",
    "WorkflowRunner",
    "graph_from_settings",
    "load_graph_file",
    "load_graph_json",
    "parse_graph",
]
