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

from pydantic import BaseModel, ValidationError

from mangomas.agents._prompt import (
    build_messages,
    resolve_llm,
    resolve_sampling,
    resolve_system_prompt,
)
from mangomas.agents._streaming import stream_with_buffered_fallback
from mangomas.cognitive.constants import COGNITIVE_SINK_EXTRAS_KEY
from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE, DEFAULT_VALIDATE_OUTPUT
from mangomas.core.agent import AgentContext, AgentRequest, AgentResponse, Message
from mangomas.core.structured import build_structured_prompt
from mangomas.errors import LLMBadResponse

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
    :func:`~mangomas.core.structured.build_structured_prompt`. With neither
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
        self._schema = schema
        # Opt-in post-completion schema validation (spec-0014 shape: the
        # resolved AgentSettings flow in from composition.py unchanged).
        # ``None`` settings → DEFAULT_VALIDATE_OUTPUT (off), preserving the
        # raw pass-through contract.
        self._validate_output: bool = (
            settings.validate_output if settings is not None else DEFAULT_VALIDATE_OUTPUT
        )
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
        self._temperature, self._max_tokens = resolve_sampling(settings)

    def _build_messages(self, request: AgentRequest) -> list[Message]:
        """Prepend the schema-aware system prompt when absent from the request."""
        return build_messages(request, self._system_prompt)

    def parse(self, content: str) -> BaseModel:
        """Validate *content* against the agent's schema and return the model.

        Raises :class:`~mangomas.errors.LLMBadResponse` when *content* is not
        valid JSON or does not conform to the schema. The error (and the log
        record emitted before it) carries the agent name, the schema class
        name, and a detail truncated to
        :data:`~mangomas.config.DEFAULT_ERROR_DETAIL_TRUNCATE` — never the
        full LLM content.
        """
        try:
            return self._schema.model_validate_json(content)
        except ValidationError as exc:
            schema_name = self._schema.__name__
            logger.error(
                "%s: LLM output failed %s validation (content length %d, head %r)",
                self.name,
                schema_name,
                len(content),
                content[:DEFAULT_ERROR_DETAIL_TRUNCATE],
            )
            raise LLMBadResponse(
                f"Agent {self.name!r}: LLM output does not conform to the {schema_name} schema.",
                detail=str(exc)[:DEFAULT_ERROR_DETAIL_TRUNCATE],
            ) from exc

    async def handle(self, request: AgentRequest, ctx: AgentContext) -> AgentResponse:
        """Return the LLM's raw structured-JSON content for the given request.

        When ``settings.validate_output`` is True the content is first checked
        via :meth:`parse` (raising
        :class:`~mangomas.errors.LLMBadResponse` on mismatch); the parsed
        object is discarded, so a valid response is byte-for-byte identical to
        the unvalidated pass-through. The streaming path is untouched —
        validating a token stream is out of scope for this flag.
        """
        messages = self._build_messages(request)
        logger.debug("%s: calling LLM for structured output", self.name)
        llm = resolve_llm(ctx, self.name)
        content = await llm.complete(
            messages,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        if self._validate_output:
            self.parse(content)
        response = AgentResponse(content=content, agent=self.name)
        await self._maybe_emit_cognitive_signal(request, ctx, content)
        return response

    async def _maybe_emit_cognitive_signal(
        self,
        request: AgentRequest,
        ctx: AgentContext,
        content: str,
    ) -> None:
        """Emit a CognitiveSignal when a sink is wired; no-op otherwise.

        The contracts package is imported only inside the producer, and only
        when extras actually carries a sink — flag-off handle stays identical.
        """
        if ctx.extras.get(COGNITIVE_SINK_EXTRAS_KEY) is None:
            return
        from mangomas.cognitive.producer import emit_agent_signal  # noqa: PLC0415

        await emit_agent_signal(
            agent_name=self.name,
            content=content,
            request=request,
            ctx=ctx,
        )

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
        llm = resolve_llm(ctx, self.name)
        async for chunk in stream_with_buffered_fallback(
            self.name,
            messages,
            llm,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        ):
            yield chunk
