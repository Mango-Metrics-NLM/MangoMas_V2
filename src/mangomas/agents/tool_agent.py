"""Tool-aware agent: parses LLM output for structured tool calls and executes them."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from mangomas.agents._prompt import build_messages, resolve_system_prompt
from mangomas.config import DEFAULT_TOOL_MAX_STEPS
from mangomas.core.agent import AgentContext, AgentRequest, AgentResponse, Message
from mangomas.core.tools import ToolCallParser, build_tool_system_prompt
from mangomas.errors import ToolExecutionError, ToolNotFound, UnknownProvider

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
        self._temperature: float | None = settings.temperature if settings is not None else None
        self._max_tokens: int | None = settings.max_tokens if settings is not None else None
        if max_tool_steps is not None:
            self._max_tool_steps = max_tool_steps
        elif settings is not None and settings.max_tool_steps is not None:
            self._max_tool_steps = settings.max_tool_steps
        else:
            self._max_tool_steps = DEFAULT_TOOL_MAX_STEPS
        self._parser = ToolCallParser()

    async def handle(self, request: AgentRequest, ctx: AgentContext) -> AgentResponse:
        """Process *request*, executing any tool calls until a plain response is produced.

        Issues at most ``max_tool_steps`` LLM calls in total;
        ``metadata["tool_steps"]`` reports the exact number made.
        """
        # Build tool-aware system prompt when tools are registered.
        tool_prompt: str | None = None
        if ctx.tools is not None:
            specs = [ctx.tools.get(n).spec for n in ctx.tools.available()]
            tool_prompt = build_tool_system_prompt(specs)

        # A custom system prompt must not displace the tool-format prompt —
        # concatenate custom-first (mirroring PlannerAgent) so the LLM always
        # learns the tool-call JSON contract when tools are registered.
        # Reuses resolve_system_prompt's suffix-combination: settings=None
        # means precedence is moot here — self._system_prompt was already
        # resolved once in __init__.
        effective_prompt = resolve_system_prompt(self._system_prompt, None, suffix=tool_prompt)
        messages = build_messages(request, effective_prompt)

        content = ""
        steps = 0
        while steps < self._max_tool_steps:
            steps += 1
            logger.debug("ToolAgent step %d/%d", steps, self._max_tool_steps)
            content = await ctx.llm.complete(
                messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
            tool_call = self._parser.parse(content)

            if tool_call is None:
                logger.debug("ToolAgent: no tool call detected, returning response")
                break

            if steps == self._max_tool_steps:
                logger.debug(
                    "ToolAgent: LLM-call budget exhausted; returning last response "
                    "without executing tool %r",
                    tool_call.tool,
                )
                break

            logger.debug("ToolAgent: dispatching tool %r", tool_call.tool)

            # Resolve the tool — re-raise UnknownProvider as ToolNotFound.
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

            # Execute — wrap unexpected exceptions as ToolExecutionError.
            try:
                result_output = await tool.execute(tool_call.arguments)
            except ToolExecutionError:
                logger.exception(
                    "Tool raised ToolExecutionError",
                    extra={"tool_name": tool_call.tool},
                )
                raise
            except Exception as exc:
                logger.exception(
                    "Tool execution failed",
                    extra={"tool_name": tool_call.tool},
                )
                raise ToolExecutionError(
                    f"Tool {tool_call.tool!r} raised: {exc}",
                    tool_name=tool_call.tool,
                ) from exc

            # Re-inject: assistant (tool call) then tool result.
            messages = [
                *messages,
                Message(role="assistant", content=content),
                Message(
                    role="tool",
                    content=json.dumps({"tool": tool_call.tool, "output": result_output}),
                ),
            ]

        # ``steps`` equals the number of LLM calls actually made (<= max_tool_steps).
        return AgentResponse(
            content=content,
            agent=self.name,
            metadata={"tool_steps": steps},
        )
