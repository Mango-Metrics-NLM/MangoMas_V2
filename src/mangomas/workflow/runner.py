"""Execute a declarative :class:`WorkflowGraph` against an ``Orchestrator``.

The runner is a *thin planner of topologies*: it compiles the graph's
topological levels (:meth:`WorkflowGraph.execution_levels`) onto the existing
orchestrator primitives. A single-node level dispatches that node (with an
optional acceptance loop); a multi-node level fans out in parallel and joins the
results. Between levels the joined output is threaded as the next level's input,
byte-for-byte matching :meth:`~mangomas.core.Orchestrator.dispatch_pipeline` for
a linear graph. No new execution engine is introduced. See spec 0005 / ADR-0007.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, cast

from opentelemetry import trace

from mangomas.core.agent import AgentRequest, AgentResponse, Message
from mangomas.errors import AgentNotFound
from mangomas.workflow.models import JOIN_CONCAT, WorkflowGraph, WorkflowNode

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import Orchestrator
    from mangomas.core.loop import AcceptanceFn

logger = logging.getLogger(__name__)

# Synthetic agent name stamped on a joined fan-out response, so a downstream
# consumer can tell the content came from a workflow join rather than one agent.
_FAN_OUT_AGENT_PREFIX = "workflow.fan_out"


def _stop_marker_acceptance(marker: str) -> AcceptanceFn:
    """Return an ``AcceptanceFn`` that accepts once *marker* appears in the content.

    A named factory (rather than an inline ``lambda``) keeps the closure typed
    and avoids ruff's ``E731`` lambda-assignment rule.
    """

    def _accept(response: AgentResponse) -> bool:
        return marker in response.content

    return _accept


class WorkflowRunner:
    """Execute a validated :class:`WorkflowGraph` against an ``Orchestrator``."""

    def __init__(self, graph: WorkflowGraph) -> None:
        self._graph = graph

    @property
    def graph(self) -> WorkflowGraph:
        """The graph this runner executes."""
        return self._graph

    async def run(self, request: AgentRequest, *, orch: Orchestrator) -> AgentResponse:
        """Execute the graph and return the final level's (joined) response.

        Fails fast with :class:`~mangomas.errors.AgentNotFound` when any node
        names an agent the orchestrator has not registered — validated up front,
        before any dispatch runs, so a typo can never partially execute a graph.
        """
        self._require_agents(orch)
        levels = self._graph.execution_levels()

        with trace.get_tracer(__name__).start_as_current_span("workflow.run") as span:
            span.set_attribute("workflow.name", self._graph.name)
            span.set_attribute("workflow.node_count", len(self._graph.nodes))
            span.set_attribute("workflow.level_count", len(levels))
            span.set_attribute("workflow.join", self._graph.join)
            logger.info(
                "Workflow run starting",
                extra={
                    "event": "workflow_start",
                    "workflow": self._graph.name,
                    "nodes": len(self._graph.nodes),
                    "levels": len(levels),
                },
            )

            current_request = request
            response: AgentResponse | None = None
            for depth, level in enumerate(levels):
                response = await self._run_level(level, current_request, orch, depth=depth)
                if depth < len(levels) - 1:
                    # Thread the level's output into the next level exactly as
                    # dispatch_pipeline threads one agent's output into the next.
                    current_request = AgentRequest(
                        messages=[Message(role="user", content=response.content)],
                        metadata=response.metadata,
                    )

            assert response is not None  # noqa: S101 — guaranteed: nodes is non-empty → >=1 level
            span.set_attribute("workflow.final_agent", response.agent)

        logger.info(
            "Workflow run complete",
            extra={
                "event": "workflow_complete",
                "workflow": self._graph.name,
                "final_agent": response.agent,
            },
        )
        return response

    def _require_agents(self, orch: Orchestrator) -> None:
        registered = set(orch.list_agents())
        for node in self._graph.nodes:
            if node.agent not in registered:
                logger.error(
                    "Workflow references an unregistered agent",
                    extra={
                        "event": "workflow_unknown_agent",
                        "workflow": self._graph.name,
                        "node": node.id,
                        "agent": node.agent,
                        "registered": sorted(registered),
                    },
                )
                raise AgentNotFound(node.agent)

    async def _run_level(
        self,
        level: tuple[WorkflowNode, ...],
        request: AgentRequest,
        orch: Orchestrator,
        *,
        depth: int,
    ) -> AgentResponse:
        if len(level) == 1:
            return await self._run_node(level[0], request, orch)
        # A node with an acceptance loop (until/max_steps) cannot go through
        # dispatch_fan_out — that primitive dispatches each agent exactly once.
        looping = any(node.until is not None or node.max_steps is not None for node in level)
        logger.debug(
            "Workflow fan-out level",
            extra={
                "event": "workflow_fan_out",
                "workflow": self._graph.name,
                "depth": depth,
                "nodes": [node.id for node in level],
                "delegated": not looping,
            },
        )
        if looping:
            responses = cast(
                "list[AgentResponse]",
                await asyncio.gather(*(self._run_node(node, request, orch) for node in level)),
            )
        else:
            # Delegate to the orchestrator primitive so the fan-out inherits its
            # dedicated span; results return in level (agent-name) order.
            responses = await orch.dispatch_fan_out([node.agent for node in level], request)
        return self._join_level(level, responses)

    async def _run_node(
        self, node: WorkflowNode, request: AgentRequest, orch: Orchestrator
    ) -> AgentResponse:
        acceptance_fn: AcceptanceFn | None = (
            _stop_marker_acceptance(node.until) if node.until is not None else None
        )
        logger.debug(
            "Workflow node dispatch",
            extra={
                "event": "workflow_node",
                "workflow": self._graph.name,
                "node": node.id,
                "agent": node.agent,
                "until": node.until,
                "max_steps": node.max_steps,
            },
        )
        return await orch.dispatch(
            node.agent, request, acceptance_fn=acceptance_fn, max_steps=node.max_steps
        )

    def _join_level(
        self, level: tuple[WorkflowNode, ...], responses: list[AgentResponse]
    ) -> AgentResponse:
        if self._graph.join == JOIN_CONCAT:
            content = "\n".join(response.content for response in responses)
        else:  # JOIN_FIRST
            content = responses[0].content
        return AgentResponse(
            content=content,
            agent=f"{_FAN_OUT_AGENT_PREFIX}:{self._graph.name}",
            metadata={
                "fan_out": {
                    "join": self._graph.join,
                    "nodes": [node.id for node in level],
                    "agents": [node.agent for node in level],
                }
            },
        )
