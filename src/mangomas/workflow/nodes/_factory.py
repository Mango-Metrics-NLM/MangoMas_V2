"""Shared typed-factory builder for workflow node executors.

Every built-in node module used to end with an identical hand-written factory:
an ``isinstance`` guard raising :class:`~mangomas.errors.ConfigError` on a
mismatched node, then the executor construction. :func:`make_node_factory`
hosts that shape once so the guard is written (and tested) in a single place.

The guard is unreachable through :func:`~mangomas.workflow.registry.resolve_executor`
(the ``kind`` discriminator routes each node to its own factory) but keeps a
direct factory call with the wrong node type fail-loud.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from mangomas.errors import ConfigError

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

    from mangomas.workflow.executor import NodeExecutor
    from mangomas.workflow.graph import WorkflowNode

NodeT = TypeVar("NodeT", bound="WorkflowNode")


def make_node_factory(
    kind: str,
    node_cls: type[NodeT],
    executor_cls: Callable[[NodeT], NodeExecutor],
) -> Callable[[WorkflowNode], NodeExecutor]:
    """Return a registry factory building *executor_cls* after a type guard on *node_cls*."""

    def factory(node: WorkflowNode) -> NodeExecutor:
        if not isinstance(node, node_cls):
            raise ConfigError(f"{kind} executor requires a {node_cls.__name__}; got {node.kind!r}")
        return executor_cls(node)

    return factory
