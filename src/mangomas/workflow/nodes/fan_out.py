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

from mangomas.config import LLM_PROFILE_METADATA_KEY
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
                    metadata=_carry_run_scope(request),
                )
            return responses[0]


def _carry_run_scope(request: AgentRequest) -> dict[str, object]:
    """Metadata for a synthesised ``concat`` response: the run-scoped selector only.

    A joined reply has no single agent's metadata to keep, but a ``sequence``
    threads this response's metadata into the next step's request
    (``nodes/sequence.py``). Dropping ``llm_profile`` here would silently run
    every node *after* a concat fan-out on the default client while the
    branches ran on the selected profile (spec-0040 R5). Nothing else from the
    inbound request is carried: the join stays metadata-empty apart from the
    selector, exactly as before for a run that names no profile.
    """
    profile = request.metadata.get(LLM_PROFILE_METADATA_KEY)
    if isinstance(profile, str):
        return {LLM_PROFILE_METADATA_KEY: profile}
    return {}


register_node("fan_out", FanOutNode, FanOutNodeExecutor)
