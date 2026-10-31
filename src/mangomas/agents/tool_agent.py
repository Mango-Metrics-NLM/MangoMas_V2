"""Tool-aware agent: parses LLM output for structured tool calls and executes them."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from mangomas.agents._prompt import (
    build_messages,
    resolve_llm,
    resolve_sampling,
    resolve_system_prompt,
)
from mangomas.config import DEFAULT_TOOL_MAX_STEPS
from mangomas.core.agent import AgentContext, AgentRequest, AgentResponse, Message
from mangomas.core.tools import ToolCall, ToolCallParser, build_tool_system_prompt
from mangomas.errors import LLMBadResponse, ToolExecutionError, ToolNotFound, UnknownProvider

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.config import AgentSettings

logger = logging.getLogger(__name__)


class ToolAgent:
    """Agent that can invoke registered tools via structured LLM output.

    The inner tool-execution loop (controlled by *max_tool_steps*) is distinct
    from the outer dispatch-loop steps managed by the :class:`Orchestrator`.
    ``max_tool_steps`` bounds the *total* number of LLM calls per request;
    resolution order is: explicit constructor argument, then
    ``settings.max_tool_steps``, then :data:`DEFAULT_TOOL_MAX_STEPS`.
    """

    name = "tool"

    def __init__(
        self,
        system_prompt: str | None = None,
        max_tool_steps: int | None = None,
        settings: AgentSettings | None = None,
    ) -> None:
        resolved_prompt = resolve_system_prompt(system_prompt, settings)
        # Blank/whitespace-only prompts are treated as unset: resolve_system_prompt
        # preserves the raw value (no truthiness collapsing) for its other four
        # callers, but ToolAgent concatenates this base with a tool-format suffix
        # below — an empty-but-not-None base would otherwise leave a stray
        # leading "\n\n" (or bare whitespace) in front of the tool prompt.
        self._system_prompt: str | None = (
            resolved_prompt.strip() or None if resolved_prompt is not None else None
        )
        self._temperature, self._max_tokens = resolve_sampling(settings)
        if max_tool_steps is not None:
            self._max_tool_steps = max_tool_steps
        elif settings is not None and settings.max_tool_steps is not None:
            self._max_tool_steps = settings.max_tool_steps
        else:
            self._max_tool_steps = DEFAULT_TOOL_MAX_STEPS
        self._parser = ToolCallParser()

    def _build_tool_messages(self, request: AgentRequest, ctx: AgentContext) -> list[Message]:
        """System prompt plus history, with the tool contract appended when tools exist."""
        tool_prompt: str | None = None
        if ctx.tools is not None:
            specs = [ctx.tools.get(name).spec for name in ctx.tools.available()]
            tool_prompt = build_tool_system_prompt(specs)
        # settings=None: self._system_prompt was resolved once in __init__.
        effective_prompt = resolve_system_prompt(self._system_prompt, None, suffix=tool_prompt)
        return build_messages(request, effective_prompt)

    def _parse_step_tool_call(
        self,
        content: str,
        *,
        ctx: AgentContext,
        step: int,
    ) -> ToolCall | None:
        """Parse one completion. No registry means a non-tool JSON block is content."""
        try:
            tool_call = self._parser.parse(content)
        except LLMBadResponse:
            if ctx.tools is not None:
                logger.warning(
                    "ToolAgent: malformed tool call JSON",
                    extra={"event": "tool_agent_bad_tool_call", "step": step},
                )
                raise
            logger.debug(
                "ToolAgent: no tool registry; treating JSON block as content",
                extra={"event": "tool_agent_no_registry_json", "step": step},
            )
            return None
        if tool_call is None:
            logger.debug("ToolAgent: no tool call detected, returning response")
        return tool_call

    async def _dispatch_tool(self, tool_call: ToolCall, ctx: AgentContext) -> str:
        """Resolve and run *tool_call*, mapping registry misses to ``ToolNotFound``."""
        if ctx.tools is None:
            logger.error(
                "Tool call requested but no tool registry is configured",
                extra={"tool_name": tool_call.tool},
            )
            raise ToolNotFound(tool_call.tool, [])
        try:
            tool = ctx.tools.get(tool_call.tool)
        except UnknownProvider:
            logger.error(
                "Tool call requested unknown tool",
                extra={
                    "tool_name": tool_call.tool,
                    "available_tools": ctx.tools.available(),
                },
            )
            raise ToolNotFound(tool_call.tool, ctx.tools.available()) from None
        try:
            return await tool.execute(tool_call.arguments)
        except ToolExecutionError:
            logger.exception("Tool raised ToolExecutionError", extra={"tool_name": tool_call.tool})
            raise
        except Exception as exc:
            logger.exception("Tool execution failed", extra={"tool_name": tool_call.tool})
            raise ToolExecutionError(
                f"Tool {tool_call.tool!r} raised: {exc}",
                tool_name=tool_call.tool,
            ) from exc

    async def handle(self, request: AgentRequest, ctx: AgentContext) -> AgentResponse:
        """Process *request*, executing any tool calls until a plain response is produced.

        Issues at most ``max_tool_steps`` LLM calls in total;
        ``metadata["tool_steps"]`` reports the exact number made.
        """
        messages = self._build_tool_messages(request, ctx)
        # Resolved once per request: the request's llm_profile (spec-0040), else
        # the agent-scoped override (if configured), else ctx.llm — every LLM
        # call in the inner loop below uses the same client (ADR-0028).
        llm = resolve_llm(ctx, self.name, request)
        content = ""
        steps = 0
        while steps < self._max_tool_steps:
            steps += 1
            logger.debug("ToolAgent step %d/%d", steps, self._max_tool_steps)
            content = await llm.complete(
                messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
            tool_call = self._parse_step_tool_call(content, ctx=ctx, step=steps)
            if tool_call is None:
                break
            if steps == self._max_tool_steps:
                logger.debug(
                    "ToolAgent: LLM-call budget exhausted; returning last response "
                    "without executing tool %r",
                    tool_call.tool,
                )
                break
            logger.debug("ToolAgent: dispatching tool %r", tool_call.tool)
            result_output = await self._dispatch_tool(tool_call, ctx)
            messages = [
                *messages,
                Message(role="assistant", content=content),
                Message(
                    role="tool",
                    content=json.dumps({"tool": tool_call.tool, "output": result_output}),
                ),
            ]
        return AgentResponse(
            content=content,
            agent=self.name,
            metadata={"tool_steps": steps},
        )
