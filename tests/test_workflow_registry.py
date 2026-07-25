"""Tests for the workflow node-kind registry."""

from __future__ import annotations

import pytest

from mangomas.errors import ConfigError, UnknownProvider
from mangomas.workflow import node_registry, resolve_executor
from mangomas.workflow.graph import AgentNode
from tests.constants import WORKFLOW_NODE_KINDS


def test_builtin_kinds_registered() -> None:
    assert sorted(node_registry.available()) == list(WORKFLOW_NODE_KINDS)


def test_resolve_executor_returns_runnable() -> None:
    executor = resolve_executor(AgentNode(agent="chat"))
    assert hasattr(executor, "run")


def test_scoped_swaps_a_kind() -> None:
    sentinel = object()

    def _fake_factory(_node: object) -> object:
        return sentinel

    with node_registry.scoped("agent", _fake_factory):  # type: ignore[arg-type]
        assert node_registry.get("agent")(AgentNode(agent="chat")) is sentinel
    # Restored after the context exits.
    assert node_registry.get("agent")(AgentNode(agent="chat")) is not sentinel


def test_unknown_kind_raises_unknown_provider() -> None:
    with pytest.raises(UnknownProvider):
        node_registry.get("does-not-exist")


def test_factory_rejects_mismatched_node_type() -> None:
    # The kind discriminator normally routes each node to its own factory; a
    # direct factory call with the wrong node type must fail loud (ConfigError).
    with pytest.raises(ConfigError, match="sequence executor requires a SequenceNode"):
        node_registry.get("sequence")(AgentNode(agent="chat"))
