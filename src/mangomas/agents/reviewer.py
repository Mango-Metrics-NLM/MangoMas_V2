"""ReviewerAgent: evaluates output and returns a structured review result."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from mangomas.agents._structured import StructuredOutputAgent

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.config import AgentSettings


class ReviewResult(BaseModel):
    """Structured review produced by the ReviewerAgent."""

    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    feedback: str
    suggestions: list[str] = Field(default_factory=list)


class ReviewerAgent(StructuredOutputAgent):
    """Agent that reviews content and returns a structured :class:`ReviewResult`.

    The system prompt includes the ``ReviewResult`` JSON schema so the LLM
    knows the exact output shape.  The raw JSON content is returned as-is;
    callers parse via ``ReviewResult.model_validate_json()``.
    """

    def __init__(
        self,
        system_prompt: str | None = None,
        settings: AgentSettings | None = None,
    ) -> None:
        super().__init__(ReviewResult, "reviewer", system_prompt, settings)
