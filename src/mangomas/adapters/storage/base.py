"""TurnRepository Protocol — the only storage surface the orchestrator depends on.

Keeping the Protocol here (rather than in ``core/``) maintains the
``adapters → core`` dependency direction while avoiding circular imports
between ``core/agent.py`` and ``adapters/storage/sqlite.py``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from mangomas.core.agent import AgentRequest, AgentResponse


@runtime_checkable
class TurnRepository(Protocol):
    """Persist and retrieve conversation turns."""

    async def save_turn(
        self,
        agent: str,
        request: AgentRequest,
        response: AgentResponse,
    ) -> int:
        """Persist one turn and return its assigned row ID."""
        ...

    async def list_turns(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent *limit* turns, newest first."""
        ...

    def close(self) -> None:
        """Release any held resources (e.g., file handles, connections)."""
        ...


@runtime_checkable
class MemoryRepository(Protocol):
    """Persistent episodic memory and index for an agent."""

    async def write_episodic(self, content: str, *, prefix: str = "") -> str:
        """Append *content* to a dated episodic file; return the file path written."""
        ...

    async def read_index(self) -> str:
        """Return the current index document contents (empty string if absent)."""
        ...

    async def append_index(self, entry: str) -> None:
        """Append *entry* as a new line in the index document."""
        ...

    def close(self) -> None:
        """Release any held resources."""
        ...
