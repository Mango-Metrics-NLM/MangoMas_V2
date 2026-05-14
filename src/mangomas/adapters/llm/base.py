"""LLM client protocols — the only surface other modules depend on."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from mangomas.core.agent import Message


@runtime_checkable
class LLMClient(Protocol):
    """Minimal async chat-completion contract."""

    async def complete(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
    ) -> str:
        """Return the assistant content for ``messages``."""
        ...

    async def aclose(self) -> None:
        """Release underlying resources."""
        ...


@runtime_checkable
class PingableLLMClient(LLMClient, Protocol):
    """Extension protocol for LLM clients that expose a health-check endpoint."""

    async def ping(self) -> None:
        """Raise an exception if the LLM service is unreachable."""
        ...


@runtime_checkable
class StreamingLLMClient(LLMClient, Protocol):
    """Extension protocol for LLM clients that support token-level streaming.

    Call ``await client.stream(messages)`` to obtain an ``AsyncIterator[str]``
    that yields content tokens as they are produced.
    """

    async def stream(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        """Return an async iterator that yields content tokens."""
        ...
