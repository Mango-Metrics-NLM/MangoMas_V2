"""TurnRepository Protocol — the only storage surface the orchestrator depends on.

Keeping the Protocol here (rather than in ``core/``) maintains the
``adapters → core`` dependency direction while avoiding circular imports
between ``core/agent.py`` and ``adapters/storage/sqlite.py``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from mangomas.config import DEFAULT_STORAGE_LIST_TURNS_LIMIT
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

    async def list_turns(
        self, limit: int = DEFAULT_STORAGE_LIST_TURNS_LIMIT
    ) -> list[dict[str, Any]]:
        """Return the most recent *limit* turns, newest first."""
        ...

    def close(self) -> None:
        """Release any held resources (e.g., file handles, connections)."""
        ...


@runtime_checkable
class AsyncCloseableRepository(TurnRepository, Protocol):
    """TurnRepository whose resources require async teardown (e.g. asyncpg pools).

    The FastAPI lifespan and CLI close paths dispatch on ``hasattr(repo,
    "aclose")`` and prefer this method when present, falling back to the
    synchronous :meth:`TurnRepository.close` otherwise. Strict extension —
    existing :class:`SQLiteRepository` continues to satisfy the bare
    :class:`TurnRepository` protocol without change.
    """

    async def aclose(self) -> None:
        """Asynchronously release held resources (e.g. close a connection pool)."""
        ...


@runtime_checkable
class FailureRecordingRepository(TurnRepository, Protocol):
    """TurnRepository that can also record a dispatch that did *not* succeed.

    Strict extension, mirroring :class:`AsyncCloseableRepository`: callers
    dispatch on ``hasattr(repo, "save_failed_turn")`` and simply skip the
    record when a repository does not offer it, so a third-party backend
    written against the bare :class:`TurnRepository` keeps satisfying its
    protocol unchanged (ADR-0031).

    Exists because ``dispatch`` only ever reached ``save_turn`` on the success
    path, so the sole durable log of the system's behaviour recorded successes
    and nothing else.
    """

    async def save_failed_turn(
        self,
        agent: str,
        request: AgentRequest,
        *,
        error_code: str,
        error: str,
    ) -> int:
        """Persist a terminal failure for *request*; return its assigned row ID.

        *error_code* is the typed ``MangomasError.code`` so failures group
        without parsing prose; *error* is the human-readable detail.
        """
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
