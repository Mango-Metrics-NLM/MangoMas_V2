"""Agent factory registration.

Seeds the default in-process agents into the agent registry.
Optional entry-point discovery can add to this registry later without
changing build_orchestrator().
"""

from __future__ import annotations

import logging

from mangomas.agents import ChatAgent, PlannerAgent, ReviewerAgent, SummarizeAgent, ToolAgent
from mangomas.composition._registries import agent_registry

logger = logging.getLogger(__name__)


def _register_default_agents() -> None:
    """Register the default in-process agents.

    Called during build_orchestrator() to seed the agent registry.
    """
    agent_registry.register("chat", lambda settings: ChatAgent(settings=settings))
    agent_registry.register("summarize", lambda settings: SummarizeAgent(settings=settings))
    agent_registry.register("tool", lambda settings: ToolAgent(settings=settings))
    agent_registry.register("planner", lambda settings: PlannerAgent(settings=settings))
    agent_registry.register("reviewer", lambda settings: ReviewerAgent(settings=settings))
    logger.debug("Default agents registered: chat, summarize, tool, planner, reviewer")


# Seed the default agents at import time (same as original composition.py)
_register_default_agents()

__all__ = [
    "_register_default_agents",
]
