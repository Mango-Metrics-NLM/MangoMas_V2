"""Agent target — dispatch a registered agent (the default eval target).

Preserves today's behaviour exactly: :meth:`run` calls
``orch.dispatch(agent, request)`` and returns ``response.content``. ``name`` is
the agent name, so an :class:`~mangomas.eval.runner.EvalReport`'s ``agent_name``
field stays byte-identical to pre-target-indirection runs.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mangomas.errors import ConfigError
from mangomas.eval.target import Target
from mangomas.eval.target_registry import target_registry

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import AgentRequest, Orchestrator

logger = logging.getLogger(__name__)


class AgentTarget:
    """Dispatch a single registered agent by name."""

    def __init__(self, agent: str) -> None:
        self._agent = agent
        self.name = agent

    @classmethod
    def from_name(cls, agent: str) -> AgentTarget:
        """Build a target for *agent* — the runner's default resolution path."""
        return cls(agent)

    async def run(self, request: AgentRequest, *, orch: Orchestrator) -> str:
        response = await orch.dispatch(self._agent, request)
        return response.content


def _agent_target_factory(options: dict[str, Any]) -> Target:
    agent = options.get("agent")
    if not agent:
        raise ConfigError("agent target requires an 'agent' option (the agent name)")
    return AgentTarget(str(agent))


target_registry.register("agent", _agent_target_factory)
