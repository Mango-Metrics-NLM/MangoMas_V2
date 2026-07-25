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


@pytest.mark.parametrize("kind", [k for k in WORKFLOW_NODE_KINDS if k != "agent"])
def test_every_factory_rejects_a_mismatched_node_type(kind: str) -> None:
    # The kind discriminator normally routes each node to its own factory; a
    # direct factory call with the wrong node type must fail loud (ConfigError).
    # Asserted per kind: all five share one closure body, so a single case would
    # leave the other four guards unexercised.
    with pytest.raises(ConfigError, match=f"{kind} executor requires an? "):
        node_registry.get(kind)(AgentNode(agent="chat"))


@pytest.mark.parametrize("kind", WORKFLOW_NODE_KINDS)
def test_registered_factory_is_named_after_its_kind(kind: str) -> None:
    # Shared-closure factories would otherwise all report the same qualname,
    # making a traceback unable to say which node kind failed.
    assert node_registry.get(kind).__name__ == f"_{kind}_factory"
