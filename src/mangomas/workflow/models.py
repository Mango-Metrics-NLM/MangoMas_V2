"""Declarative multi-agent workflow graph — pure domain models.

A :class:`WorkflowGraph` is a directed acyclic graph of agent invocations. Each
:class:`WorkflowNode` names a registered agent and declares which other nodes it
depends on (``depends_on`` — the graph's edges). The graph compiles onto the
existing orchestrator primitives (``dispatch`` / ``dispatch_fan_out`` / the
``AcceptanceFn`` loop) via :meth:`WorkflowGraph.execution_levels`; it introduces
**no new execution engine**. See spec 0005 and ADR-0007.

These models are *pure* — they import nothing from the orchestrator or adapters —
so parsing and validation are testable without any runtime wiring. Execution
semantics live in :class:`mangomas.workflow.runner.WorkflowRunner`.

Execution model (v1): the graph is decomposed into **topological levels**
(Kahn's algorithm). Nodes in the same level have no ordering constraint between
them and run in parallel (fan-out); levels run in sequence, each seeded by the
previous level's joined output (pipeline). A linear graph therefore reproduces
:meth:`~mangomas.core.Orchestrator.dispatch_pipeline` exactly. Per-edge (rather
than per-level) data routing and conditional edges are deliberate follow-ups
(see the spec's open questions).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ── Domain defaults (single source of truth for the graph model) ──────────────
# Env-driven *enablement* lives in ``WorkflowSettings`` (mangomas.config); these
# are per-graph defaults applied when a definition omits the field.

JoinStrategy = Literal["first", "concat"]

JOIN_FIRST: JoinStrategy = "first"
JOIN_CONCAT: JoinStrategy = "concat"

# A workflow fan-out is usually a gather-then-synthesise pattern, so the default
# join concatenates every branch's output (vs. the eval ``fan_out`` target which
# defaults to ``first`` because it scores a single prediction). See ADR-0007.
DEFAULT_WORKFLOW_JOIN: JoinStrategy = JOIN_CONCAT
DEFAULT_WORKFLOW_NAME: str = "workflow"


class WorkflowNode(BaseModel):
    """One agent invocation in a :class:`WorkflowGraph`.

    ``depends_on`` lists the ids of nodes whose output must precede this node —
    the graph's edges. ``until`` + ``max_steps`` describe an optional acceptance
    loop: the node's agent is dispatched repeatedly until ``until`` appears in
    the response content or ``max_steps`` is exhausted (compiled onto the
    orchestrator's existing ``acceptance_fn`` loop).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, description="Unique node id within the graph.")
    agent: str = Field(min_length=1, description="Name of the registered agent to dispatch.")
    depends_on: tuple[str, ...] = Field(
        default=(),
        description="Ids of predecessor nodes (edges); empty for a source node.",
    )
    until: str | None = Field(
        default=None,
        description="Acceptance stop-marker: loop until this substring appears in the content.",
    )
    max_steps: int | None = Field(
        default=None,
        ge=1,
        description="Loop cap for an acceptance node (required when 'until' is set).",
    )

    @model_validator(mode="after")
    def _validate_node(self) -> WorkflowNode:
        if self.until is not None and self.max_steps is None:
            raise ValueError(f"node {self.id!r}: 'max_steps' is required when 'until' is set")
        return self


class WorkflowGraph(BaseModel):
    """A validated, acyclic graph of :class:`WorkflowNode`.

    Structural invariants (unique ids, edges referencing existing nodes, no
    self-edges, acyclicity) are enforced at construction, so an invalid graph
    can never reach the runner. ``join`` selects how a fan-out level's parallel
    outputs are merged before feeding the next level.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(default=DEFAULT_WORKFLOW_NAME, min_length=1)
    join: JoinStrategy = DEFAULT_WORKFLOW_JOIN
    nodes: tuple[WorkflowNode, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_graph(self) -> WorkflowGraph:
        ids: set[str] = set()
        for node in self.nodes:
            if node.id in ids:
                raise ValueError(f"duplicate node id: {node.id!r}")
            ids.add(node.id)
        for node in self.nodes:
            for dep in node.depends_on:
                if dep == node.id:
                    raise ValueError(f"node {node.id!r} depends on itself")
                if dep not in ids:
                    raise ValueError(f"node {node.id!r} depends on unknown node {dep!r}")
        # Computing the levels raises ValueError on a cycle — reuse it as the
        # acyclicity check so the two share one implementation.
        self._compute_levels()
        return self

    def execution_levels(self) -> list[tuple[WorkflowNode, ...]]:
        """Return the topological levels to execute, in order.

        Level ``k`` contains every node all of whose dependencies were resolved
        in levels ``< k``. Nodes within a level run in parallel (fan-out); levels
        run sequentially (pipeline). Declaration order is preserved within a
        level for deterministic execution and telemetry.
        """
        return self._compute_levels()

    def _compute_levels(self) -> list[tuple[WorkflowNode, ...]]:
        by_id = {node.id: node for node in self.nodes}
        order = {node.id: index for index, node in enumerate(self.nodes)}
        indegree = {node.id: len(node.depends_on) for node in self.nodes}
        successors: dict[str, list[str]] = {node.id: [] for node in self.nodes}
        for node in self.nodes:
            for dep in node.depends_on:
                successors[dep].append(node.id)

        ready = sorted((nid for nid, deg in indegree.items() if deg == 0), key=order.__getitem__)
        levels: list[tuple[WorkflowNode, ...]] = []
        processed = 0
        while ready:
            levels.append(tuple(by_id[nid] for nid in ready))
            processed += len(ready)
            nxt: list[str] = []
            for nid in ready:
                for succ in successors[nid]:
                    indegree[succ] -= 1
                    if indegree[succ] == 0:
                        nxt.append(succ)
            ready = sorted(nxt, key=order.__getitem__)

        if processed != len(self.nodes):
            raise ValueError("workflow graph contains a cycle")
        return levels
