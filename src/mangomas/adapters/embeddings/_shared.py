"""Shared mixins for embedding adapters.

Every embedding backend implements batching (``embed_batch``) natively; the
single-text ``embed`` is always the same one-element delegation, and backends
without an owned transport share the same no-op ``aclose``. Hosting both here
keeps each adapter down to its genuinely backend-specific ``embed_batch``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover

    class _HasEmbedBatch(Protocol):
        async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


class SingleTextEmbedMixin:
    """Derive single-text ``embed`` from the adapter's ``embed_batch``."""

    async def embed(self: _HasEmbedBatch, text: str) -> list[float]:
        """Return the embedding vector for a single ``text``."""
        vectors = await self.embed_batch([text])
        return vectors[0]


class NoTransportAcloseMixin:
    """No-op ``aclose`` for backends that own no sockets or transport."""

    async def aclose(self) -> None:
        """Nothing to release; satisfies the ``EmbeddingClient`` protocol."""
        return None
