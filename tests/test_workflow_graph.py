"""Tests for the frozen workflow-graph domain model (parse-time validation)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mangomas.config import DEFAULT_WORKFLOW_LOOP_MAX_STEPS, DEFAULT_WORKFLOW_SCHEMA_VERSION
from mangomas.workflow import WorkflowGraph
from mangomas.workflow.graph import AgentNode, FanOutNode, LoopNode, SequenceNode
from mangomas.workflow.predicate import PredicateSpec


def _accept() -> PredicateSpec:
    return PredicateSpec(kind="contains", value="DONE")


def test_valid_nested_graph_parses() -> None:
    graph = WorkflowGraph(
        name="demo",
        root=SequenceNode(
            steps=[
                AgentNode(agent="planner"),
                FanOutNode(branches=[AgentNode(agent="reviewer")], join="concat"),
                LoopNode(agent="chat", accept=_accept(), max_steps=3),
            ]
        ),
    )
    assert graph.schema_version == DEFAULT_WORKFLOW_SCHEMA_VERSION
    assert graph.root.kind == "sequence"
    assert [step.kind for step in graph.root.steps] == ["agent", "fan_out", "loop"]


def test_loop_default_max_steps() -> None:
    node = LoopNode(agent="chat", accept=_accept())
    assert node.max_steps == DEFAULT_WORKFLOW_LOOP_MAX_STEPS


def test_agent_node_from_dict_discriminates_by_kind() -> None:
    graph = WorkflowGraph.model_validate({"name": "x", "root": {"kind": "agent", "agent": "chat"}})
    assert isinstance(graph.root, AgentNode)
    assert graph.root.agent == "chat"


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkflowGraph.model_validate({"name": "x", "root": {"kind": "bogus"}})


def test_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkflowGraph.model_validate(
            {"name": "x", "root": {"kind": "agent", "agent": "chat", "oops": 1}}
        )


def test_empty_sequence_steps_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkflowGraph.model_validate({"name": "x", "root": {"kind": "sequence", "steps": []}})


def test_empty_fan_out_branches_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkflowGraph.model_validate({"name": "x", "root": {"kind": "fan_out", "branches": []}})


def test_loop_max_steps_below_one_rejected() -> None:
    with pytest.raises(ValidationError):
        LoopNode(agent="chat", accept=_accept(), max_steps=0)


def test_blank_agent_name_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentNode(agent="")


def test_sequence_step_cannot_be_a_sequence() -> None:
    """v1 bounds nesting: a sequence step is agent|fan_out|loop, never a sequence."""
    with pytest.raises(ValidationError):
        WorkflowGraph.model_validate(
            {
                "name": "x",
                "root": {
                    "kind": "sequence",
                    "steps": [{"kind": "sequence", "steps": [{"kind": "agent", "agent": "c"}]}],
                },
            }
        )


def test_fan_out_branch_must_be_agent() -> None:
    """Branches are agents only in v1 (no composite fan-out)."""
    with pytest.raises(ValidationError):
        WorkflowGraph.model_validate(
            {
                "name": "x",
                "root": {
                    "kind": "fan_out",
                    "branches": [
                        {"kind": "fan_out", "branches": [{"kind": "agent", "agent": "c"}]}
                    ],
                },
            }
        )


def test_graph_is_frozen() -> None:
    node = AgentNode(agent="chat")
    with pytest.raises(ValidationError):
        node.agent = "other"  # type: ignore[misc]
