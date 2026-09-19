"""Frozen Pydantic models for a declarative multi-agent workflow graph.

The graph is a **bounded tree**: composition lives only in a ``sequence`` node
whose steps are each an ``agent``, a ``fan_out`` (over agents), or a ``loop``
(over one agent). Every leaf therefore maps 1:1 onto a single public
``Orchestrator`` dispatch call, so the executor never reimplements pipeline
threading, fan-out ``gather``, or the acceptance loop. The tree is acyclic by
construction — no cycle detection is needed.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from mangomas.config import DEFAULT_WORKFLOW_LOOP_MAX_STEPS, DEFAULT_WORKFLOW_SCHEMA_VERSION
from mangomas.workflow.predicate import PredicateSpec


class _NodeBase(BaseModel):
    """Shared frozen / strict config for every node model."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class AgentNode(_NodeBase):
    """Dispatch a single registered agent."""

    kind: Literal["agent"] = "agent"
    agent: str = Field(min_length=1)


class FanOutNode(_NodeBase):
    """Fan several branches out in parallel and reduce their replies.

    Each branch is any :data:`WorkflowStep` (an ``agent`` / ``fan_out`` / ``loop``
    / ``branch``). An **all-``agent``** fan_out delegates to
    ``dispatch_fan_out`` verbatim (parity); a composite branch is run via its
    executor (ADR-0018). ``join`` selects the reduction: ``first`` returns the
    first branch's response verbatim; ``concat`` newline-joins every branch's
    ``content`` into a fresh response (``agent="fan_out"``, empty metadata).
    """

    kind: Literal["fan_out"] = "fan_out"
    branches: list[WorkflowStep] = Field(min_length=1)
    join: Literal["first", "concat"] = "first"


class LoopNode(_NodeBase):
    """Iterate a single agent until an acceptance predicate holds."""

    kind: Literal["loop"] = "loop"
    agent: str = Field(min_length=1)
    accept: PredicateSpec
    max_steps: int = Field(default=DEFAULT_WORKFLOW_LOOP_MAX_STEPS, ge=1)


class BranchCase(_NodeBase):
    """A predicate-guarded case: run ``then`` when ``when`` matches the input."""

    when: PredicateSpec
    then: WorkflowStep


class BranchNode(_NodeBase):
    """Select exactly one child by predicate (first match wins).

    ``when`` predicates are evaluated in declared order against the node's input
    content (the last threaded message); the first match's ``then`` runs.
    ``default`` runs when no case matches — with no ``default`` an unmatched
    branch raises :class:`~mangomas.errors.ConfigError`. The node selects one
    child and adds no back-edge, so the graph stays an acyclic tree (ADR-0016).
    """

    kind: Literal["branch"] = "branch"
    branches: list[BranchCase] = Field(min_length=1)
    default: WorkflowStep | None = None


# A ``sequence`` step is any leaf node — but NOT another ``sequence`` (v1 keeps
# nesting bounded to depth two, so no recursion / cycle is possible). A ``branch``
# is a step too (it selects one child, adding no cycle).
WorkflowStep = Annotated[
    AgentNode | FanOutNode | LoopNode | BranchNode, Field(discriminator="kind")
]


class SequenceNode(_NodeBase):
    """Thread an ordered list of steps; each step's output feeds the next."""

    kind: Literal["sequence"] = "sequence"
    steps: list[WorkflowStep] = Field(min_length=1)


WorkflowNode = Annotated[
    AgentNode | FanOutNode | LoopNode | BranchNode | SequenceNode,
    Field(discriminator="kind"),
]


class WorkflowGraph(BaseModel):
    """A named, declarative workflow graph with a single root node."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = DEFAULT_WORKFLOW_SCHEMA_VERSION
    name: str = Field(min_length=1)
    root: WorkflowNode


# Resolve the discriminated-union annotations eagerly so a malformed graph fails
# at load time rather than at first validation deep inside a run.
WorkflowGraph.model_rebuild()


def iter_nodes(node: WorkflowNode) -> Iterator[WorkflowNode]:
    """Yield *node* and every node beneath it, depth-first, parents before children.

    A reusable static walk over the graph. The tree is bounded but genuinely
    recursive: a ``fan_out`` branch may itself be a ``fan_out`` or ``branch``
    (spec 0013 / ADR-0018), so a two-level loop misses nodes. The tree is acyclic
    by construction — ``sequence`` cannot nest and ``branch`` adds no back-edge —
    so no ``seen`` set is needed.

    Kept here beside the models rather than in a consumer, because it depends
    only on the node shapes and every static check over a graph wants it. The
    executor's :func:`~mangomas.workflow.registry.resolve_executor` recursion is
    the *runtime* counterpart and deliberately separate: it resolves one child at
    a time as it dispatches, and cannot enumerate a graph without running it.
    """
    yield node
    if isinstance(node, SequenceNode):
        children: tuple[WorkflowStep, ...] = tuple(node.steps)
    elif isinstance(node, FanOutNode):
        children = tuple(node.branches)
    elif isinstance(node, BranchNode):
        children = tuple(case.then for case in node.branches)
        if node.default is not None:
            children += (node.default,)
    else:
        # ``agent`` and ``loop`` are leaves: a loop iterates one agent by name and
        # holds no child node.
        return
    for child in children:
        yield from iter_nodes(child)
