"""PlannerAgent: produces a structured execution plan via JSON-schema-constrained LLM output."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from mangomas.core.agent import AgentContext, AgentRequest, AgentResponse, Message
from mangomas.core.tools import build_structured_prompt

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.config import AgentSettings

logger = logging.getLogger(__name__)


class PlanStep(BaseModel):
    """A single step in an execution plan."""

    step: int = Field(ge=1)
    description: str
    agent: str | None = None


class ExecutionPlan(BaseModel):
    """Structured output produced by the PlannerAgent."""

    goal: str
    steps: list[PlanStep] = Field(min_length=1)


class PlannerAgent:
    """Agent that converts a goal into a structured execution plan.

    The system prompt includes the ``ExecutionPlan`` JSON schema so the LLM
    knows the exact output shape expected.  The raw content is returned as-is;
    callers are responsible for parsing via ``ExecutionPlan.model_validate_json()``.
    """

    name = "planner"

    def __init__(
        self,
        system_prompt: str | None = None,
        settings: AgentSettings | None = None,
    ) -> None:
        base_prompt = build_structured_prompt(ExecutionPlan.model_json_schema())
        if system_prompt is not None:
            self._system_prompt = f"{system_prompt}\n\n{base_prompt}"
        elif settings is not None and settings.system_prompt is not None:
            self._system_prompt = f"{settings.system_prompt}\n\n{base_prompt}"
        else:
            self._system_prompt = base_prompt

    async def handle(self, request: AgentRequest, ctx: AgentContext) -> AgentResponse:
        """Return a structured JSON plan for the given request."""
        messages = list(request.messages)
        if not any(m.role == "system" for m in messages):
            messages.insert(0, Message(role="system", content=self._system_prompt))

        logger.debug("PlannerAgent: calling LLM for plan")
        content = await ctx.llm.complete(messages)
        return AgentResponse(content=content, agent=self.name)
