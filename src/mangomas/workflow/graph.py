"""Frozen Pydantic models for a declarative multi-agent workflow graph.

The graph is a **bounded tree**: composition lives only in a ``sequence`` node
whose steps are each an ``agent``, a ``fan_out`` (over agents), or a ``loop``
(over one agent). Every leaf therefore maps 1:1 onto a single public
``Orchestrator`` dispatch call, so the executor never reimplements pipeline
threading, fan-out ``gather``, or the acceptance loop. The tree is acyclic by
construction — no cycle detection is needed.
"""

from __future__ import annotations

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
    """Dispatch several agents in parallel and reduce their replies.

    ``join`` selects the reduction: ``first`` returns the first branch's response
    verbatim; ``concat`` newline-joins every branch's ``content`` into a fresh
    response (``agent="fan_out"``, empty metadata).
    """

    kind: Literal["fan_out"] = "fan_out"
    branches: list[AgentNode] = Field(min_length=1)
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
