"""Echo target — a deterministic, no-LLM baseline.

Used as a regression baseline (Capability B) and as a fast, dependency-free test
fixture. When ``text`` is configured it is returned verbatim for every row;
otherwise the content of the last user message is echoed (empty string when the
request carries no user turn). The orchestrator is never consulted, so the
target is fully deterministic.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mangomas.eval.target import Target
from mangomas.eval.target_registry import target_registry

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import AgentRequest, Orchestrator

logger = logging.getLogger(__name__)


class EchoTarget:
    """Return a fixed string, or echo the last user message."""

    name = "echo"

    def __init__(self, *, text: str | None = None) -> None:
        self._text = text

    async def run(
        self,
        request: AgentRequest,
        *,
        orch: Orchestrator,  # noqa: ARG002 — deterministic; no dispatch
    ) -> str:
        if self._text is not None:
            return self._text
        for message in reversed(request.messages):
            if message.role == "user":
                return message.content
        return ""


def _echo_target_factory(options: dict[str, Any]) -> Target:
    text = options.get("text")
    return EchoTarget(text=str(text) if text is not None else None)


target_registry.register("echo", _echo_target_factory)
