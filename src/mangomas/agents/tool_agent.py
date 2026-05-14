"""Tool-aware agent: parses LLM output for structured tool calls and executes them."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

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
    """

    name = "tool"

    def __init__(
        self,
        system_prompt: str | None = None,
        max_tool_steps: int = 5,
        settings: AgentSettings | None = None,
    ) -> None:
        self._system_prompt: str | None = (
            settings.system_prompt
            if settings is not None and settings.system_prompt is not None
            else system_prompt
        )
        self._max_tool_steps = max_tool_steps
        self._parser = ToolCallParser()

    async def handle(self, request: AgentRequest, ctx: AgentContext) -> AgentResponse:
        """Process *request*, executing any tool calls until a plain response is produced."""
        messages = list(request.messages)

        # Build tool-aware system prompt when tools are registered.
        tool_prompt: str | None = None
        if ctx.tools is not None:
            specs = [ctx.tools.get(n).spec for n in ctx.tools.available()]
            tool_prompt = build_tool_system_prompt(specs)

        effective_prompt = self._system_prompt or tool_prompt
        if effective_prompt and not any(m.role == "system" for m in messages):
            messages.insert(0, Message(role="system", content=effective_prompt))

        for step in range(self._max_tool_steps):
            logger.debug("ToolAgent step %d/%d", step + 1, self._max_tool_steps)
            content = await ctx.llm.complete(messages)
            tool_call = self._parser.parse(content)

            if tool_call is None:
                logger.debug("ToolAgent: no tool call detected, returning response")
                return AgentResponse(
                    content=content,
                    agent=self.name,
                    metadata={"tool_steps": step + 1},
                )

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
                logger.error(
                    "Tool raised ToolExecutionError",
                    extra={"tool_name": tool_call.tool},
                    exc_info=True,
                )
                raise
            except Exception as exc:
                logger.error(
                    "Tool execution failed",
                    extra={"tool_name": tool_call.tool},
                    exc_info=True,
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

        # Tool steps exhausted: get a final plain response.
        content = await ctx.llm.complete(messages)
        return AgentResponse(
            content=content,
            agent=self.name,
            metadata={"tool_steps": self._max_tool_steps},
        )
