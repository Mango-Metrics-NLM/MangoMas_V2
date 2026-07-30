"""Shared typed-factory builder + registration for workflow node executors.

Every built-in node module used to end with an identical hand-written factory:
an ``isinstance`` guard raising :class:`~mangomas.errors.ConfigError` on a
mismatched node, then the executor construction. :func:`make_node_factory`
hosts that shape once so the guard is written (and tested) in a single place,
and :func:`register_node` both builds the factory and registers it in
:data:`~mangomas.workflow.registry.node_registry` — so a node module states its
kind literal exactly once.

The guard is unreachable through :func:`~mangomas.workflow.registry.resolve_executor`
(the ``kind`` discriminator routes each node to its own factory) but keeps a
direct factory call with the wrong node type fail-loud.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from mangomas.errors import ConfigError
from mangomas.workflow.registry import node_registry

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

    # Name the closure after the kind it serves: without this every registered
    # factory reports the same `make_node_factory.<locals>.factory` qualname in
    # tracebacks and registry reprs, so a failure cannot be traced to its node.
    factory.__name__ = f"_{kind}_factory"
    factory.__qualname__ = factory.__name__
    return factory


def register_node(
    kind: str,
    node_cls: type[NodeT],
    executor_cls: Callable[[NodeT], NodeExecutor],
) -> None:
    """Build the typed factory for *kind* and register it in ``node_registry``.

    The single call per node module states the kind literal once — the registry
    key, the factory name, and the guard message all derive from the same
    argument, so they can never drift apart.
    """
    node_registry.register(kind, make_node_factory(kind, node_cls, executor_cls))
