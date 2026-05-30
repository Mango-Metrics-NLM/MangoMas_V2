"""Domain models for the RAG layer — immutable value objects.

These are the RAG layer's own vocabulary; the vector adapter speaks in
primitives (:class:`~mangomas.adapters.vector.base.VectorMatch`) and the
retriever maps between the two. Keeping them separate preserves the
``rag → adapters (protocols only)`` dependency direction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Chunk:
    """A single embeddable slice of a source document.

    ``id`` is stable across re-ingestion (``{source}#{index}``) so an upsert
    replaces the prior version of the same chunk rather than duplicating it.
    """

    id: str
    text: str
    source: str
    index: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchResult:
    """A retrieved chunk paired with its similarity score in ``[0, 1]``."""

    chunk: Chunk
    score: float
