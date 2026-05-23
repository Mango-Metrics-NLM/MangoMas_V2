"""Default chat agent: forwards the conversation to the configured LLM."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, AsyncIterator
from typing import TYPE_CHECKING

from mangomas.agents._streaming import stream_with_buffered_fallback
from mangomas.core.agent import AgentContext, AgentRequest, AgentResponse, Message

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
        self._system_prompt: str | None = (
            settings.system_prompt
            if settings is not None and settings.system_prompt is not None
            else system_prompt
        )

    def _build_messages(self, request: AgentRequest) -> list[Message]:
        """Prepend system prompt when absent from the request."""
        messages = list(request.messages)
        if self._system_prompt and not any(m.role == "system" for m in messages):
            messages.insert(0, Message(role="system", content=self._system_prompt))
        return messages

    async def handle(self, request: AgentRequest, ctx: AgentContext) -> AgentResponse:
        """Send the messages to the LLM and wrap the reply."""
        messages = self._build_messages(request)
        logger.debug("ChatAgent dispatching %d messages", len(messages))
        content = await ctx.llm.complete(messages)
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
        messages = self._build_messages(request)
        logger.debug("ChatAgent streaming %d messages", len(messages))
        async for chunk in stream_with_buffered_fallback(self.name, messages, ctx):
            yield chunk
