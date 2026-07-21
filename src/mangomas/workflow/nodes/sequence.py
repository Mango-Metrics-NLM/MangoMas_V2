"""``sequence`` node — thread steps so each output feeds the next input.

Threads like :meth:`Orchestrator.dispatch_pipeline`: the next request is
``AgentRequest(messages=[Message("user", resp.content)], metadata=<copy of resp.metadata>)``
with no ``max_steps`` carried forward. The metadata is copied per step for
isolation (the values are unchanged), so a sequence whose steps are all ``agent``
nodes produces a result identical to ``dispatch_pipeline([names], request)``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from opentelemetry import trace

from mangomas.core import AgentRequest, Message
from mangomas.errors import ConfigError
from mangomas.workflow.graph import SequenceNode
from mangomas.workflow.registry import node_registry, resolve_executor

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import AgentResponse, Orchestrator
    from mangomas.workflow.executor import NodeExecutor
    from mangomas.workflow.graph import WorkflowNode


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
                    # Copy the metadata so a downstream step mutating its request
                    # can never corrupt the prior step's response (the values are
                    # unchanged, so result parity with dispatch_pipeline holds).
                    current = AgentRequest(
                        messages=[Message(role="user", content=response.content)],
                        metadata=dict(response.metadata),
                    )
        assert response is not None  # noqa: S101 — steps is non-empty (min_length=1)
        return response


def _sequence_factory(node: WorkflowNode) -> NodeExecutor:
    if not isinstance(node, SequenceNode):  # pragma: no cover — guarded by the kind discriminator
        raise ConfigError(f"sequence executor requires a SequenceNode; got {node.kind!r}")
    return SequenceNodeExecutor(node)


node_registry.register("sequence", _sequence_factory)
