"""Shared streaming-with-fallback helper for agents.

Three agents (``ChatAgent``, ``PlannerAgent``, ``ReviewerAgent``) implement
``stream()`` with the identical buffered-fallback pattern: when the
configured LLM client implements :class:`StreamingLLMClient` the tokens
flow through; otherwise the agent calls ``complete()`` and yields the full
response as a single chunk, logging a warning so operators can spot the
degradation.

This module factors that pattern into a single async generator —
:func:`stream_with_buffered_fallback` — that each agent's ``_do_stream``
delegates to once it has built its own message list. Each agent retains
ownership of its prompt-construction logic; only the streaming-vs-fallback
branch is shared.

Adding a fourth streaming agent only requires implementing ``_build_messages``
and calling :func:`stream_with_buffered_fallback` from ``_do_stream``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from mangomas.adapters.llm.base import StreamingLLMClient
from mangomas.core.agent import AgentContext, Message

logger = logging.getLogger(__name__)

_FALLBACK_WARNING_MESSAGE: str = (
    "Streaming requested but LLM client does not support streaming; using complete() fallback"
)


async def stream_with_buffered_fallback(
    agent_name: str,
    messages: list[Message],
    ctx: AgentContext,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> AsyncGenerator[str, None]:
    """Yield LLM tokens for *messages*, with a buffered ``complete()`` fallback.

    Parameters
    ----------
    agent_name:
        The agent's ``name`` attribute. Surfaced in the warning log's
        ``extra={"agent": ...}`` field so operators can see which agent's
        streaming path is degraded.
    messages:
        Already-prepared message list (the caller is responsible for
        prepending its system prompt — this helper does not modify it).
    ctx:
        The agent context whose ``llm`` will be inspected for streaming
        capability.
    temperature:
        Forwarded verbatim to ``ctx.llm.stream``/``ctx.llm.complete`` (the
        caller resolves this from ``AgentSettings``; ``None`` lets the LLM
        adapter apply its own default).
    max_tokens:
        Forwarded verbatim to ``ctx.llm.stream``/``ctx.llm.complete``, same
        resolution contract as *temperature*.

    Behaviour
    ---------
    * If ``ctx.llm`` implements :class:`StreamingLLMClient` the helper
      simply forwards its token stream.
    * Otherwise it logs ``WARNING`` once, calls ``ctx.llm.complete(messages)``,
      and yields the full response as a single chunk so callers see a
      uniform "stream" of one element.
    """
    if isinstance(ctx.llm, StreamingLLMClient):
        async for chunk in await ctx.llm.stream(
            messages, temperature=temperature, max_tokens=max_tokens
        ):
            yield chunk
        return

    logger.warning(
        _FALLBACK_WARNING_MESSAGE,
        extra={"agent": agent_name},
    )
    content = await ctx.llm.complete(messages, temperature=temperature, max_tokens=max_tokens)
    yield content
