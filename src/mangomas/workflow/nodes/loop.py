"""``loop`` node — iterate one agent until an acceptance predicate holds."""

from __future__ import annotations

from typing import TYPE_CHECKING

from opentelemetry import trace

from mangomas.workflow.graph import LoopNode
from mangomas.workflow.nodes._factory import register_node
from mangomas.workflow.predicate import compile_predicate

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import AgentRequest, AgentResponse, Orchestrator


class LoopNodeExecutor:
    """Delegate to :meth:`Orchestrator.dispatch` with a compiled acceptance loop.

    ``dispatch`` raises :class:`~mangomas.errors.MaxStepsExceeded` when the
    predicate never accepts within ``max_steps`` — v1 has no best-effort mode.
    """

    def __init__(self, node: LoopNode) -> None:
        self._agent = node.agent
        self._max_steps = node.max_steps
        self._accept = compile_predicate(node.accept)

    async def run(self, request: AgentRequest, *, orch: Orchestrator) -> AgentResponse:
        with trace.get_tracer(__name__).start_as_current_span("workflow.node.loop") as span:
            span.set_attribute("agent.name", self._agent)
            span.set_attribute("loop.max_steps", self._max_steps)
            return await orch.dispatch(
                self._agent,
                request,
                acceptance_fn=self._accept,
                max_steps=self._max_steps,
            )


register_node("loop", LoopNode, LoopNodeExecutor)
