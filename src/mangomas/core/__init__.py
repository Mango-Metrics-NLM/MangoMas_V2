"""Core domain: agent contract + orchestrator."""

from __future__ import annotations

from mangomas.core.agent import (
    Agent,
    AgentContext,
    AgentRequest,
    AgentResponse,
    Message,
    StreamingAgent,
)
from mangomas.core.loop import AcceptanceFn
from mangomas.core.orchestrator import Orchestrator

__all__ = [
    "AcceptanceFn",
    "Agent",
    "AgentContext",
    "AgentRequest",
    "AgentResponse",
    "Message",
    "Orchestrator",
    "StreamingAgent",
]
