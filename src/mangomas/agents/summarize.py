"""Summarize agent: condenses recent conversation history into a brief summary."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mangomas.core.agent import AgentContext, AgentRequest, AgentResponse, Message

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.config import AgentSettings

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = (
    "You are a concise conversation summarizer. "
    "Given a series of conversation turns, produce a short, clear summary of "
    "the key topics discussed and any outcomes or action items identified."
)
_DEFAULT_HISTORY_LIMIT: int = 10


def _format_turns(turns: list[dict[str, Any]]) -> str:
    """Render persisted turns as a human-readable string (oldest turn first).

    ``list_turns`` returns newest-first; we reverse for chronological order.
    """
    lines: list[str] = []
    for turn in reversed(turns):
        agent_name: str = turn.get("agent", "?")
        request_data: dict[str, Any] = turn.get("request", {})
        response_data: dict[str, Any] = turn.get("response", {})
        messages: list[dict[str, Any]] = request_data.get("messages", [])
        user_msg: str = next(
            (str(m.get("content", "")) for m in messages if m.get("role") == "user"),
            "",
        )
        reply: str = str(response_data.get("content", ""))
        lines.append(f"[{agent_name}] User: {user_msg}")
        lines.append(f"[{agent_name}] Assistant: {reply}")
    return "\n".join(lines)


class SummarizeAgent:
    """Fetches recent conversation history and asks the LLM to summarise it.

    Satisfies the :class:`~mangomas.core.agent.Agent` protocol.

    Parameters
    ----------
    system_prompt:
        Override the default summarization system prompt.
    history_limit:
        Maximum number of past turns to include in the summary context.
    settings:
        Optional per-agent :class:`~mangomas.config.AgentSettings`; its
        ``system_prompt`` field takes precedence over *system_prompt* when set.
    """

    name = "summarize"

    def __init__(
        self,
        system_prompt: str | None = None,
        history_limit: int = _DEFAULT_HISTORY_LIMIT,
        settings: AgentSettings | None = None,
    ) -> None:
        effective_system = (
            settings.system_prompt
            if settings is not None and settings.system_prompt is not None
            else system_prompt
        )
        self._system_prompt: str = effective_system or _DEFAULT_SYSTEM_PROMPT
        self._history_limit: int = history_limit

    async def handle(self, request: AgentRequest, ctx: AgentContext) -> AgentResponse:
        """Fetch recent history and ask the LLM to summarise it."""
        history_text = ""
        if ctx.repo is not None:
            turns = await ctx.repo.list_turns(limit=self._history_limit)
            history_text = _format_turns(turns)
            logger.debug(
                "SummarizeAgent loaded %d history turns",
                len(turns),
                extra={"agent": self.name, "history_turns": len(turns)},
            )

        # Build the user content: combine the caller's message with history.
        user_content: str = request.messages[-1].content if request.messages else ""
        if history_text:
            user_content = (
                f"{user_content}\n\n---\nConversation history:\n{history_text}"
                if user_content
                else f"Conversation history:\n{history_text}"
            )
        if not user_content:
            user_content = "Please summarize the conversation so far."

        messages: list[Message] = [
            Message(role="system", content=self._system_prompt),
            Message(role="user", content=user_content),
        ]

        content = await ctx.llm.complete(messages)
        logger.debug(
            "SummarizeAgent completed summary",
            extra={"agent": self.name},
        )
        return AgentResponse(content=content, agent=self.name)
