"""Tests for the pure-domain workflow graph models (spec 0005 / ADR-0007)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mangomas.workflow.models import (
    DEFAULT_WORKFLOW_JOIN,
    DEFAULT_WORKFLOW_NAME,
    JOIN_CONCAT,
    JOIN_FIRST,
    WorkflowGraph,
    WorkflowNode,
)


def _ids(levels: list[tuple[WorkflowNode, ...]]) -> list[list[str]]:
    return [[node.id for node in level] for level in levels]


def _linear() -> WorkflowGraph:
    return WorkflowGraph(
        nodes=(
            WorkflowNode(id="a", agent="planner"),
            WorkflowNode(id="b", agent="tool", depends_on=("a",)),
            WorkflowNode(id="c", agent="reviewer", depends_on=("b",)),
        )
    )


# ── Defaults + constants ──────────────────────────────────────────────────────


def test_defaults() -> None:
    graph = WorkflowGraph(nodes=(WorkflowNode(id="a", agent="chat"),))
    assert graph.name == DEFAULT_WORKFLOW_NAME
    assert graph.join == DEFAULT_WORKFLOW_JOIN == JOIN_CONCAT
    assert JOIN_FIRST == "first"


def test_node_is_hashable() -> None:
    """Frozen models are hashable — cheap proof that ``frozen=True`` holds."""
    assert isinstance(hash(WorkflowNode(id="a", agent="chat")), int)


# ── Level decomposition ───────────────────────────────────────────────────────


def test_linear_levels_are_singletons() -> None:
    assert _ids(_linear().execution_levels()) == [["a"], ["b"], ["c"]]


def test_fan_out_same_level() -> None:
    graph = WorkflowGraph(
        nodes=(
            WorkflowNode(id="a", agent="reviewer"),
            WorkflowNode(id="b", agent="summarize"),
        )
    )
    assert _ids(graph.execution_levels()) == [["a", "b"]]


def test_diamond_levels() -> None:
    graph = WorkflowGraph(
        nodes=(
            WorkflowNode(id="s", agent="planner"),
            WorkflowNode(id="a", agent="reviewer", depends_on=("s",)),
            WorkflowNode(id="b", agent="summarize", depends_on=("s",)),
            WorkflowNode(id="j", agent="tool", depends_on=("a", "b")),
        )
    )
    assert _ids(graph.execution_levels()) == [["s"], ["a", "b"], ["j"]]


def test_independent_chains_run_as_levels() -> None:
    graph = WorkflowGraph(
        nodes=(
            WorkflowNode(id="a", agent="planner"),
            WorkflowNode(id="c", agent="tool"),
            WorkflowNode(id="b", agent="reviewer", depends_on=("a",)),
            WorkflowNode(id="d", agent="summarize", depends_on=("c",)),
        )
    )
    assert _ids(graph.execution_levels()) == [["a", "c"], ["b", "d"]]


def test_level_preserves_declaration_order() -> None:
    graph = WorkflowGraph(
        nodes=(
            WorkflowNode(id="z", agent="chat"),
            WorkflowNode(id="y", agent="chat"),
            WorkflowNode(id="x", agent="chat"),
        )
    )
    assert _ids(graph.execution_levels()) == [["z", "y", "x"]]


# ── Structural validation ─────────────────────────────────────────────────────


def test_empty_nodes_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkflowGraph(nodes=())


def test_duplicate_id_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate node id"):
        WorkflowGraph(
            nodes=(
                WorkflowNode(id="a", agent="chat"),
                WorkflowNode(id="a", agent="tool"),
            )
        )


def test_dangling_edge_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown node"):
        WorkflowGraph(nodes=(WorkflowNode(id="a", agent="chat", depends_on=("ghost",)),))


def test_self_edge_rejected() -> None:
    with pytest.raises(ValidationError, match="depends on itself"):
        WorkflowGraph(nodes=(WorkflowNode(id="a", agent="chat", depends_on=("a",)),))


def test_cycle_rejected() -> None:
    with pytest.raises(ValidationError, match="cycle"):
        WorkflowGraph(
            nodes=(
                WorkflowNode(id="a", agent="chat", depends_on=("b",)),
                WorkflowNode(id="b", agent="tool", depends_on=("a",)),
            )
        )


# ── Node acceptance-loop validation ───────────────────────────────────────────


def test_until_requires_max_steps() -> None:
    with pytest.raises(ValidationError, match="max_steps"):
        WorkflowNode(id="a", agent="chat", until="DONE")


def test_until_with_max_steps_valid() -> None:
    node = WorkflowNode(id="a", agent="chat", until="DONE", max_steps=3)
    assert node.until == "DONE"
    assert node.max_steps == 3


def test_max_steps_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        WorkflowNode(id="a", agent="chat", max_steps=0)


def test_extra_key_forbidden() -> None:
    with pytest.raises(ValidationError):
        WorkflowNode.model_validate({"id": "a", "agent": "chat", "typo": 1})
