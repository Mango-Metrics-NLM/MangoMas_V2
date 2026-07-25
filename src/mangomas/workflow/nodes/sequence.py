"""``sequence`` node — thread steps so each output feeds the next input.

Threads exactly as :meth:`Orchestrator.dispatch_pipeline`: the next request is
``AgentRequest(messages=[Message("user", resp.content)], metadata=resp.metadata)``
with no ``max_steps`` carried forward. A sequence whose steps are all ``agent``
nodes is therefore byte-identical to ``dispatch_pipeline([names], request)``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from opentelemetry import trace

from mangomas.core import AgentRequest, Message
from mangomas.workflow.graph import SequenceNode
from mangomas.workflow.nodes._factory import make_node_factory
from mangomas.workflow.registry import node_registry, resolve_executor

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import AgentResponse, Orchestrator


class SequenceNodeExecutor:
    """Run each step in order, threading its output into the next step's input."""

    def __init__(self, node: SequenceNode) -> None:
        self._steps = node.steps

    async def run(self, request: AgentRequest, *, orch: Orchestrator) -> AgentResponse:
        with trace.get_tracer(__name__).start_as_current_span("workflow.node.sequence") as span:
            span.set_attribute("step_count", len(self._steps))
            current = request
            response: AgentResponse | None = None
            last = len(self._steps) - 1
            for i, step in enumerate(self._steps):
                response = await resolve_executor(step).run(current, orch=orch)
                if i < last:
                    current = AgentRequest(
                        messages=[Message(role="user", content=response.content)],
                        metadata=response.metadata,
                    )
        assert response is not None  # noqa: S101 — steps is non-empty (min_length=1)
        return response


node_registry.register(
    "sequence", make_node_factory("sequence", SequenceNode, SequenceNodeExecutor)
)
