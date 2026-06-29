"""Concrete agents."""

from mangomas.agents.chat import ChatAgent
from mangomas.agents.discovery import (
    AGENT_ENTRY_POINT_GROUP,
    discover_agents,
    ensure_agent_plugins,
)
from mangomas.agents.planner import ExecutionPlan, PlannerAgent, PlanStep
from mangomas.agents.reviewer import ReviewerAgent, ReviewResult
from mangomas.agents.summarize import SummarizeAgent
from mangomas.agents.tool_agent import ToolAgent

__all__ = [
    "AGENT_ENTRY_POINT_GROUP",
    "ChatAgent",
    "ExecutionPlan",
    "PlanStep",
    "PlannerAgent",
    "ReviewResult",
    "ReviewerAgent",
    "SummarizeAgent",
    "ToolAgent",
    "discover_agents",
    "ensure_agent_plugins",
]
