"""``agent`` node — dispatch a single registered agent."""

from __future__ import annotations

from typing import TYPE_CHECKING

from opentelemetry import trace

from mangomas.workflow.graph import AgentNode
from mangomas.workflow.nodes._factory import make_node_factory
from mangomas.workflow.registry import node_registry

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import AgentRequest, AgentResponse, Orchestrator


class AgentNodeExecutor:
    """Delegate to :meth:`Orchestrator.dispatch` for one agent (transparent)."""

    def __init__(self, node: AgentNode) -> None:
        self._agent = node.agent

    async def run(self, request: AgentRequest, *, orch: Orchestrator) -> AgentResponse:
        with trace.get_tracer(__name__).start_as_current_span("workflow.node.agent") as span:
            span.set_attribute("agent.name", self._agent)
            return await orch.dispatch(self._agent, request)


node_registry.register("agent", make_node_factory("agent", AgentNode, AgentNodeExecutor))
