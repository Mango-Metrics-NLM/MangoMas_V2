"""Tool-aware agent: parses LLM output for structured tool calls and executes them."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

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
        # Normalize blank/whitespace-only prompts to None for consistency.
        prompt_candidate: str | None = (
            settings.system_prompt
            if settings is not None and settings.system_prompt is not None
            else system_prompt
        )
        self._system_prompt: str | None = (
            prompt_candidate.strip() if prompt_candidate else None
        ) or None
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
        messages = list(request.messages)

        # Build tool-aware system prompt when tools are registered.
        tool_prompt: str | None = None
        if ctx.tools is not None:
            specs = [ctx.tools.get(n).spec for n in ctx.tools.available()]
            tool_prompt = build_tool_system_prompt(specs)

        # A custom system prompt must not displace the tool-format prompt —
        # concatenate custom-first (mirroring PlannerAgent) so the LLM always
        # learns the tool-call JSON contract when tools are registered.
        if self._system_prompt is not None and tool_prompt is not None:
            effective_prompt: str | None = f"{self._system_prompt}\n\n{tool_prompt}"
        else:
            effective_prompt = self._system_prompt or tool_prompt
        if effective_prompt and not any(m.role == "system" for m in messages):
            messages.insert(0, Message(role="system", content=effective_prompt))

        content = ""
        steps = 0
        while steps < self._max_tool_steps:
            steps += 1
            logger.debug("ToolAgent step %d/%d", steps, self._max_tool_steps)
            content = await ctx.llm.complete(messages)
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
