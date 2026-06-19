"""Pipeline target — evaluate a sequential multi-agent pipeline.

Each agent's output feeds the next via
:meth:`~mangomas.core.Orchestrator.dispatch_pipeline`; the final agent's
``content`` is the prediction the scorer grades.
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


class PipelineTarget:
    """Dispatch an ordered list of agents as a pipeline."""

    name = "pipeline"

    def __init__(self, agents: list[str]) -> None:
        self._agents = agents

    async def run(self, request: AgentRequest, *, orch: Orchestrator) -> str:
        response = await orch.dispatch_pipeline(self._agents, request)
        return response.content


def _pipeline_target_factory(options: dict[str, Any]) -> Target:
    agents = options.get("agents")
    if not isinstance(agents, list) or not agents:
        raise ConfigError("pipeline target requires a non-empty 'agents' list")
    return PipelineTarget([str(a) for a in agents])


target_registry.register("pipeline", _pipeline_target_factory)
