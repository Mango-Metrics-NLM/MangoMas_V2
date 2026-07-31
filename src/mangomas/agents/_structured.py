"""Shared base for schema-constrained, JSON-structured-output agents.

``PlannerAgent`` and ``ReviewerAgent`` were identical except for their Pydantic
schema, agent name, and docstrings: same system-prompt-plus-schema
construction, same ``_build_messages``, same ``handle``, same buffered-fallback
``stream``/``_do_stream``. :class:`StructuredOutputAgent` collapses that into
one implementation; subclasses supply only the schema model and name (see
:class:`~mangomas.agents.planner.PlannerAgent` and
:class:`~mangomas.agents.reviewer.ReviewerAgent`).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, AsyncIterator
from typing import TYPE_CHECKING

from pydantic import BaseModel

from mangomas.agents._prompt import build_messages, resolve_system_prompt
from mangomas.agents._streaming import stream_with_buffered_fallback
from mangomas.core.agent import AgentContext, AgentRequest, AgentResponse, Message
from mangomas.core.tools import build_structured_prompt

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.config import AgentSettings

logger = logging.getLogger(__name__)


class StructuredOutputAgent:
    """Base agent that instructs the LLM to emit JSON matching a fixed schema.

    The effective system prompt is ``resolve_system_prompt(system_prompt,
    settings, suffix=<schema prompt>, prefer_explicit=True)`` — i.e. an
    explicit constructor ``system_prompt`` wins over
    ``settings.system_prompt``, and either is concatenated (custom-first)
    ahead of the schema instructions built via
    :func:`~mangomas.core.tools.build_structured_prompt`. With neither
    override, the schema prompt alone is used.

    Subclasses satisfy the :class:`~mangomas.core.agent.Agent` and
    :class:`~mangomas.core.agent.StreamingAgent` protocols by inheriting this
    class and calling ``super().__init__(<SchemaModel>, "<agent-name>",
    system_prompt, settings)`` — see
    :class:`~mangomas.agents.planner.PlannerAgent` /
    :class:`~mangomas.agents.reviewer.ReviewerAgent`.
    """

    name: str

    def __init__(
        self,
        schema: type[BaseModel],
        agent_name: str,
        system_prompt: str | None = None,
        settings: AgentSettings | None = None,
    ) -> None:
        self.name = agent_name
        schema_prompt = build_structured_prompt(schema.model_json_schema())
        # ``schema_prompt`` is always a non-empty string, so this call can
        # never actually return ``None`` — the ``or`` fallback exists purely
        # to narrow the type for mypy (see resolve_system_prompt's suffix
        # contract).
        self._system_prompt: str = (
            resolve_system_prompt(
                system_prompt,
                settings,
                suffix=schema_prompt,
                prefer_explicit=True,
            )
            or schema_prompt
        )
        self._temperature: float | None = settings.temperature if settings is not None else None
        self._max_tokens: int | None = settings.max_tokens if settings is not None else None

    def _build_messages(self, request: AgentRequest) -> list[Message]:
        """Prepend the schema-aware system prompt when absent from the request."""
        return build_messages(request, self._system_prompt)

    async def handle(self, request: AgentRequest, ctx: AgentContext) -> AgentResponse:
        """Return the LLM's raw structured-JSON content for the given request."""
        messages = self._build_messages(request)
        logger.debug("%s: calling LLM for structured output", self.name)
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
        """Return an async iterator that yields structured-output tokens from the LLM."""
        return self._do_stream(request, ctx)

    async def _do_stream(
        self,
        request: AgentRequest,
        ctx: AgentContext,
    ) -> AsyncGenerator[str, None]:
        messages = self._build_messages(request)
        logger.debug("%s streaming %d messages", self.name, len(messages))
        async for chunk in stream_with_buffered_fallback(
            self.name,
            messages,
            ctx,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        ):
            yield chunk
