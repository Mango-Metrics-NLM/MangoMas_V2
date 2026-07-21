"""Node-kind registry: maps a node ``kind`` to a :class:`NodeExecutor` factory.

Mirrors :data:`mangomas.eval.target_registry.target_registry`. Built-in node
executors self-register at import time (see :mod:`mangomas.workflow.nodes`); each
factory takes the frozen :class:`~mangomas.workflow.graph.WorkflowNode` and
returns a :class:`~mangomas.workflow.executor.NodeExecutor`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mangomas.registry import Registry

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

    from mangomas.workflow.executor import NodeExecutor
    from mangomas.workflow.graph import WorkflowNode

    NodeExecutorFactory = Callable[[WorkflowNode], NodeExecutor]

node_registry: Registry[NodeExecutorFactory] = Registry("workflow_node")


def resolve_executor(node: WorkflowNode) -> NodeExecutor:
    """Return the executor for *node*, resolved by its ``kind``.

    Raises :class:`~mangomas.errors.UnknownProvider` (a ``ConfigError`` subclass)
    if no executor is registered for the node's ``kind``.
    """
    return node_registry.get(node.kind)(node)
