"""Default chat agent: forwards the conversation to the configured LLM."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, AsyncIterator
from typing import TYPE_CHECKING

from mangomas.agents._prompt import build_messages, resolve_system_prompt
from mangomas.agents._streaming import stream_with_buffered_fallback
from mangomas.core.agent import AgentContext, AgentRequest, AgentResponse

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.config import AgentSettings

logger = logging.getLogger(__name__)


class ChatAgent:
    """Minimal pass-through agent satisfying the :class:`Agent` and
    :class:`StreamingAgent` protocols."""

    name = "chat"

    def __init__(
        self,
        system_prompt: str | None = None,
        settings: AgentSettings | None = None,
    ) -> None:
        # Per-agent settings override the constructor arg when provided.
        self._system_prompt: str | None = resolve_system_prompt(system_prompt, settings)
        self._temperature: float | None = settings.temperature if settings is not None else None
        self._max_tokens: int | None = settings.max_tokens if settings is not None else None

    async def handle(self, request: AgentRequest, ctx: AgentContext) -> AgentResponse:
        """Send the messages to the LLM and wrap the reply."""
        messages = build_messages(request, self._system_prompt)
        logger.debug("ChatAgent dispatching %d messages", len(messages))
        content = await ctx.llm.complete(
            messages,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        return AgentResponse(content=content, agent=self.name)

    async def stream(
        self,
        request: AgentRequest,
        ctx: AgentContext,
    ) -> AsyncIterator[str]:
        """Return an async iterator that yields content tokens from the LLM."""
        return self._do_stream(request, ctx)

    async def _do_stream(
        self,
        request: AgentRequest,
        ctx: AgentContext,
    ) -> AsyncGenerator[str, None]:
        messages = build_messages(request, self._system_prompt)
        logger.debug("ChatAgent streaming %d messages", len(messages))
        async for chunk in stream_with_buffered_fallback(
            self.name,
            messages,
            ctx,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        ):
            yield chunk
