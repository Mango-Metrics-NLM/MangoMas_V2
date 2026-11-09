"""Agent target — dispatch a registered agent (the default eval target).

Preserves today's behaviour exactly: :meth:`run` calls
``orch.dispatch(agent, request)`` and returns ``response.content`` as the graded
text, with ``response.metadata`` carried alongside (spec-0045). ``name`` is
the agent name, so an :class:`~mangomas.eval.runner.EvalReport`'s ``agent_name``
field stays byte-identical to pre-target-indirection runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mangomas.eval._options import require_str
from mangomas.eval.target import Target, TargetOutcome, with_usage
from mangomas.eval.target_registry import target_registry
from mangomas.utils.llm_usage import usage_scope

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import AgentRequest, Orchestrator


class AgentTarget:
    """Dispatch a single registered agent by name."""

    def __init__(self, *, agent: str) -> None:
        self._agent = agent
        self.name = agent

    async def run(self, request: AgentRequest, *, orch: Orchestrator) -> TargetOutcome:
        with usage_scope() as totals:
            response = await orch.dispatch(self._agent, request)
        return TargetOutcome(
            content=response.content, metadata=with_usage(response.metadata, totals)
        )


def _agent_target_factory(options: dict[str, Any]) -> Target:
    agent = require_str(options, "agent", owner="agent target")
    return AgentTarget(agent=agent)


target_registry.register("agent", _agent_target_factory)
