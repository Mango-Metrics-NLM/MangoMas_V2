"""``fan_out`` node — run branches in parallel and reduce their replies.

Each branch is any ``WorkflowStep``. An **all-``agent``** fan_out delegates to
``Orchestrator.dispatch_fan_out`` verbatim (parity — identical output + spans);
a composite branch (``loop`` / ``branch`` / nested ``fan_out``) is run via its
executor under ``asyncio.gather``, since ``dispatch_fan_out`` is name-based and
cannot express a composite child (ADR-0018).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from opentelemetry import trace

from mangomas.core import AgentResponse
from mangomas.workflow.graph import AgentNode, FanOutNode
from mangomas.workflow.nodes._factory import register_node
from mangomas.workflow.registry import resolve_executor

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import AgentRequest, Orchestrator
    from mangomas.workflow.graph import WorkflowStep

# Agent name stamped on the synthesized ``concat`` response (no single agent owns
# a joined reply). ``first`` returns a branch response verbatim, keeping its agent.
_FAN_OUT_AGENT = "fan_out"


class FanOutNodeExecutor:
    """Fan branches out in parallel and join the responses (``first``/``concat``)."""

    def __init__(self, node: FanOutNode) -> None:
        self._branches: list[WorkflowStep] = list(node.branches)
        self._join = node.join
        # Parity fast-path: an all-agent fan_out delegates to dispatch_fan_out
        # (identical output + spans). ``None`` ⇒ a composite branch is present.
        # The ``isinstance`` filter both narrows for the type checker and (given
        # the all-agent guard) keeps every branch.
        self._agent_names: list[str] | None = None
        if all(isinstance(b, AgentNode) for b in node.branches):
            self._agent_names = [b.agent for b in node.branches if isinstance(b, AgentNode)]

    async def run(self, request: AgentRequest, *, orch: Orchestrator) -> AgentResponse:
        with trace.get_tracer(__name__).start_as_current_span("workflow.node.fan_out") as span:
            span.set_attribute("agent_count", len(self._branches))
            span.set_attribute("join", self._join)
            span.set_attribute("composite", self._agent_names is None)
            if self._agent_names is not None:
                responses = await orch.dispatch_fan_out(self._agent_names, request)
            else:
                responses = list(
                    await asyncio.gather(
                        *(resolve_executor(b).run(request, orch=orch) for b in self._branches)
                    )
                )
            # The join is part of the node's work — keep it inside the span so
            # the span covers the full fan-out (dispatch + reduce), like every
            # other node executor.
            if self._join == "concat":
                return AgentResponse(
                    content="\n".join(r.content for r in responses),
                    agent=_FAN_OUT_AGENT,
                    metadata={},
                )
            return responses[0]


register_node("fan_out", FanOutNode, FanOutNodeExecutor)
