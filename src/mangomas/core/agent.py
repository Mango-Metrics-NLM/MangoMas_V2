"""Agent contract.

A single, narrow Protocol-based contract that every concrete agent must satisfy.
This is the only stable surface the orchestrator depends on.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # pragma: no cover
    # Imported only for type-checking; guards against circular imports at runtime.
    from mangomas.adapters.embeddings.base import EmbeddingClient
    from mangomas.adapters.llm.base import LLMClient
    from mangomas.adapters.storage.base import MemoryRepository, TurnRepository
    from mangomas.adapters.vector.base import VectorStoreRepository
    from mangomas.core.tools import ToolRegistry


class Message(BaseModel):
    """A single turn in a conversation."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str


class AgentRequest(BaseModel):
    """Input to an agent."""

    messages: list[Message]
    metadata: dict[str, Any] = Field(default_factory=dict)
    max_steps: int = Field(default=1, ge=1)


class AgentResponse(BaseModel):
    """Output from an agent."""

    content: str
    agent: str
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass
class AgentContext:
    """Runtime context injected by the orchestrator.

    Kept as a dataclass (not pydantic) so non-serializable adapters (LLM client,
    repository) can be passed without validation overhead.

    The ``llm`` and ``repo`` fields are annotated with their Protocol types
    under ``TYPE_CHECKING`` only; ``from __future__ import annotations`` keeps
    all annotations as strings at runtime, preventing circular imports.
    """

    llm: LLMClient
    repo: TurnRepository | None
    extras: dict[str, Any] = field(default_factory=dict)
    tools: ToolRegistry | None = None
    memory: MemoryRepository | None = None
    embeddings: EmbeddingClient | None = None
    vector_store: VectorStoreRepository | None = None


@runtime_checkable
class Agent(Protocol):
    """The single agent contract."""

    name: str

    async def handle(self, request: AgentRequest, ctx: AgentContext) -> AgentResponse:
        """Process a request and return a response."""
        ...


@runtime_checkable
class StreamingAgent(Agent, Protocol):
    """Extension protocol for agents that support token-level streaming.

    Agents implementing this protocol can serve the ``/agents/{name}/stream``
    endpoint.  Non-streaming agents automatically fall back to ``handle()``
    with the reply yielded as a single chunk.
    """

    async def stream(
        self,
        request: AgentRequest,
        ctx: AgentContext,
    ) -> AsyncIterator[str]:
        """Return an async iterator that yields content tokens."""
        ...
