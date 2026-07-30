"""Tests for the workflow node-kind registry."""

from __future__ import annotations

import inspect
from importlib import import_module
from pathlib import Path

import pytest

from mangomas.errors import ConfigError, UnknownProvider
from mangomas.workflow import node_registry, resolve_executor
from mangomas.workflow.graph import (
    AgentNode,
    BranchCase,
    BranchNode,
    FanOutNode,
    LoopNode,
    SequenceNode,
    WorkflowNode,
)
from mangomas.workflow.predicate import PredicateSpec
from tests.constants import DEFAULT_AGENT_NAME, WORKFLOW_NODE_KINDS


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


# One well-formed node instance per kind, for resolving through the registry.
_AGENT_NODE = AgentNode(agent=DEFAULT_AGENT_NAME)
_PREDICATE = PredicateSpec(kind="contains", value="ok")
_NODE_BY_KIND: dict[str, WorkflowNode] = {
    "agent": _AGENT_NODE,
    "branch": BranchNode(branches=[BranchCase(when=_PREDICATE, then=_AGENT_NODE)]),
    "fan_out": FanOutNode(branches=[_AGENT_NODE]),
    "loop": LoopNode(agent=DEFAULT_AGENT_NAME, accept=_PREDICATE),
    "sequence": SequenceNode(steps=[_AGENT_NODE]),
}


@pytest.mark.parametrize("kind", WORKFLOW_NODE_KINDS)
def test_every_kind_resolves_to_a_runnable_executor(kind: str) -> None:
    # register_node wired each built-in kind end-to-end: a well-formed node of
    # every kind resolves through the registry to an executor exposing run().
    executor = resolve_executor(_NODE_BY_KIND[kind])
    assert hasattr(executor, "run")


@pytest.mark.parametrize("kind", WORKFLOW_NODE_KINDS)
def test_registration_states_kind_literal_once(kind: str) -> None:
    # register_node collapsed the old `node_registry.register("<kind>",
    # make_node_factory("<kind>", ...))` line, which stated the kind literal
    # twice. Each node module must now register with a single-literal call and
    # never call make_node_factory directly.
    module = import_module(f"mangomas.workflow.nodes.{kind}")
    source = Path(inspect.getfile(module)).read_text(encoding="utf-8")
    assert source.count(f'register_node("{kind}"') == 1
    assert "make_node_factory(" not in source
