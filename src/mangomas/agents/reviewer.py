"""ReviewerAgent: evaluates output and returns a structured review result."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, AsyncIterator
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from mangomas.adapters.llm.base import StreamingLLMClient
from mangomas.core.agent import AgentContext, AgentRequest, AgentResponse, Message
from mangomas.core.tools import build_structured_prompt

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.config import AgentSettings

logger = logging.getLogger(__name__)


class ReviewResult(BaseModel):
    """Structured review produced by the ReviewerAgent."""

    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    feedback: str
    suggestions: list[str] = Field(default_factory=list)


class ReviewerAgent:
    """Agent that reviews content and returns a structured :class:`ReviewResult`.

    The system prompt includes the ``ReviewResult`` JSON schema so the LLM
    knows the exact output shape.  The raw JSON content is returned as-is;
    callers parse via ``ReviewResult.model_validate_json()``.
    """

    name = "reviewer"

    def __init__(
        self,
        system_prompt: str | None = None,
        settings: AgentSettings | None = None,
    ) -> None:
        base_prompt = build_structured_prompt(ReviewResult.model_json_schema())
        if system_prompt is not None:
            self._system_prompt = f"{system_prompt}\n\n{base_prompt}"
        elif settings is not None and settings.system_prompt is not None:
            self._system_prompt = f"{settings.system_prompt}\n\n{base_prompt}"
        else:
            self._system_prompt = base_prompt

    def _build_messages(self, request: AgentRequest) -> list[Message]:
        """Prepend the schema-aware system prompt when absent from the request."""
        messages = list(request.messages)
        if not any(m.role == "system" for m in messages):
            messages.insert(0, Message(role="system", content=self._system_prompt))
        return messages

    async def handle(self, request: AgentRequest, ctx: AgentContext) -> AgentResponse:
        """Return a structured JSON review for the given request."""
        messages = self._build_messages(request)
        logger.debug("ReviewerAgent: calling LLM for review")
        content = await ctx.llm.complete(messages)
        return AgentResponse(content=content, agent=self.name)

    async def stream(
        self,
        request: AgentRequest,
        ctx: AgentContext,
    ) -> AsyncIterator[str]:
        """Return an async iterator that yields review tokens from the LLM."""
        return self._do_stream(request, ctx)

    async def _do_stream(
        self,
        request: AgentRequest,
        ctx: AgentContext,
    ) -> AsyncGenerator[str, None]:
        messages = self._build_messages(request)
        logger.debug("ReviewerAgent streaming %d messages", len(messages))
        if isinstance(ctx.llm, StreamingLLMClient):
            async for chunk in await ctx.llm.stream(messages):
                yield chunk
        else:
            # Fallback for non-streaming LLM clients: complete and yield as one chunk.
            logger.warning(
                "Streaming requested but LLM client does not support streaming; "
                "using complete() fallback",
                extra={"agent": self.name},
            )
            content = await ctx.llm.complete(messages)
            yield content
