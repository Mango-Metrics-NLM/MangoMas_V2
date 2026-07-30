"""VectorStoreRepository Protocol — the vector-search surface the RAG layer depends on.

Kept on primitives (``ids`` / ``embeddings`` / ``documents`` / ``metadatas`` and a
:class:`VectorMatch` result) so the vector adapter never imports ``rag/``; the RAG
layer maps :class:`VectorMatch` to its own :class:`~mangomas.rag.models.SearchResult`.
This preserves the ``adapters → core`` dependency direction and avoids a
``vector ↔ rag`` cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class VectorMatch:
    """A single nearest-neighbour hit from a vector store query.

    ``score`` is a cosine *similarity* in ``[0, 1]`` — the store is responsible
    for converting its native distance metric. For Chroma's cosine space, that
    is ``1 - distance / 2`` (cosine distance lies in ``[0, 2]``), which is
    algebraically identical to the eval scorer's ``(cos + 1) / 2`` normalisation.
    """

    id: str
    document: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class VectorStoreRepository(Protocol):
    """Persist embedded documents and retrieve nearest neighbours by vector."""

    async def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """Insert or replace ``len(ids)`` records; all lists must be the same length."""
        ...

    async def query(self, *, embedding: list[float], top_k: int) -> list[VectorMatch]:
        """Return up to ``top_k`` nearest matches to ``embedding``, best first."""
        ...

    async def delete_by_source(self, source: str) -> int:
        """Remove every record whose metadata ``source`` equals ``source``.

        Returns the number of vectors removed (``0`` when the store held none
        for this source), so callers can distinguish a real cleanup from a
        no-op. Used for idempotent re-ingestion: a shortened document must not
        leave orphaned high-index chunks behind.
        """
        ...

    async def aclose(self) -> None:
        """Release any held resources (e.g. an on-disk client)."""
        ...
