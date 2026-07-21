"""``fan_out`` node — dispatch agents in parallel and reduce their replies."""

from __future__ import annotations

from typing import TYPE_CHECKING

from opentelemetry import trace

from mangomas.core import AgentResponse
from mangomas.errors import ConfigError
from mangomas.workflow.graph import FanOutNode
from mangomas.workflow.registry import node_registry

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import AgentRequest, Orchestrator
    from mangomas.workflow.executor import NodeExecutor
    from mangomas.workflow.graph import WorkflowNode

# Agent name stamped on the synthesized ``concat`` response (no single agent owns
# a joined reply). ``first`` returns a branch response verbatim, keeping its agent.
_FAN_OUT_AGENT = "fan_out"


class FanOutNodeExecutor:
    """Delegate to :meth:`Orchestrator.dispatch_fan_out` and join the responses."""

    def __init__(self, node: FanOutNode) -> None:
        self._agents = [branch.agent for branch in node.branches]
        self._join = node.join

    async def run(self, request: AgentRequest, *, orch: Orchestrator) -> AgentResponse:
        with trace.get_tracer(__name__).start_as_current_span("workflow.node.fan_out") as span:
            span.set_attribute("agent_count", len(self._agents))
            span.set_attribute("join", self._join)
            responses = await orch.dispatch_fan_out(self._agents, request)
        if self._join == "concat":
            return AgentResponse(
                content="\n".join(r.content for r in responses),
                agent=_FAN_OUT_AGENT,
                metadata={},
            )
        return responses[0]


def _fan_out_factory(node: WorkflowNode) -> NodeExecutor:
    if not isinstance(node, FanOutNode):  # pragma: no cover — guarded by the kind discriminator
        raise ConfigError(f"fan_out executor requires a FanOutNode; got {node.kind!r}")
    return FanOutNodeExecutor(node)


node_registry.register("fan_out", _fan_out_factory)
