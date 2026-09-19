"""PlannerAgent: produces a structured execution plan via JSON-schema-constrained LLM output."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, Field

from mangomas.agents._structured import StructuredOutputAgent

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.config import AgentSettings


class PlanStep(BaseModel):
    """A single step in an execution plan."""

    step: int = Field(ge=1)
    description: str
    agent: str | None = None


class ExecutionPlan(BaseModel):
    """Structured output produced by the PlannerAgent."""

    goal: str
    steps: list[PlanStep] = Field(min_length=1)


class PlannerAgent(StructuredOutputAgent):
    """Agent that converts a goal into a structured execution plan.

    The system prompt includes the ``ExecutionPlan`` JSON schema so the LLM
    knows the exact output shape expected.  The raw content is returned as-is;
    callers parse it via :meth:`parse` (inherited from
    :class:`~mangomas.agents._structured.StructuredOutputAgent`), which returns
    an :class:`ExecutionPlan` or raises
    :class:`~mangomas.errors.LLMBadResponse`. Setting
    ``MANGOMAS_AGENTS__PLANNER__VALIDATE_OUTPUT=true`` makes ``handle`` run
    that validation itself and reject malformed output.
    """

    #: Declared on the class so a workflow graph's acceptance predicate can be
    #: validated against this schema at load time without constructing an agent
    #: (see ``mangomas.workflow.validation``).
    schema: ClassVar[type[BaseModel]] = ExecutionPlan

    def __init__(
        self,
        system_prompt: str | None = None,
        settings: AgentSettings | None = None,
    ) -> None:
        super().__init__(type(self).schema, "planner", system_prompt, settings)
