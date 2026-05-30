"""Chroma vector store adapter — a persistent cosine-space collection.

The ``chromadb`` import is deferred to :meth:`ChromaVectorStore.__init__` so the
module is always importable without the optional ``rag`` extra. A ``collection``
(any object exposing ``upsert`` / ``query`` / ``delete``) may be injected for
tests; when supplied no SDK import is performed.

The collection is created with ``metadata={"hnsw:space": "cosine"}`` — Chroma's
*default* space is L2, whose unbounded distance would make ``1 - distance``
negative and violate the ``[0, 1]`` similarity contract. In cosine space the
distance ``d = 1 - cos`` lies in ``[0, 2]``, so similarity is ``1 - d / 2``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Final

from mangomas.adapters.vector.base import VectorMatch

logger = logging.getLogger(__name__)

__all__ = ["ChromaVectorStore"]

_SDK_INSTALL_HINT = (
    "chromadb is not installed. Install the optional extra: pip install 'mangomas[rag]'"
)

_COSINE_SPACE: dict[str, Any] = {"hnsw:space": "cosine"}

# Upper bound of Chroma's cosine *distance* ``d = 1 - cos`` (``cos ∈ [-1, 1]``).
# Used both to normalise distance → similarity and as the conservative default
# for any row Chroma returns without a distance.
_MAX_COSINE_DISTANCE: Final[float] = 2.0


def _lazy_collection(  # pragma: no cover - requires rag extra
    *,
    persist_dir: str,
    collection_name: str,
) -> Any:
    """Open a persistent Chroma client and return a cosine-space collection.

    Excluded from coverage because it requires the optional ``rag`` extra; the
    injected-collection path used by unit tests bypasses this helper.
    """
    try:
        # Lazy: optional ``rag`` extra; deferring keeps the module importable
        # without chromadb installed.
        import chromadb  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(_SDK_INSTALL_HINT) from exc
    client = chromadb.PersistentClient(path=persist_dir)
    return client.get_or_create_collection(name=collection_name, metadata=_COSINE_SPACE)


def _similarity_from_distance(distance: float) -> float:
    """Convert a Chroma cosine distance ``[0, 2]`` to a similarity ``[0, 1]``."""
    return max(0.0, min(1.0, 1.0 - distance / _MAX_COSINE_DISTANCE))


class ChromaVectorStore:
    """Vector store backed by a persistent ChromaDB cosine-space collection."""

    def __init__(
        self,
        *,
        persist_dir: str,
        collection_name: str,
        collection: Any | None = None,
    ) -> None:
        self._persist_dir = persist_dir
        self._collection_name = collection_name
        if collection is not None:
            self._collection = collection
        else:
            self._collection = _lazy_collection(
                persist_dir=persist_dir,
                collection_name=collection_name,
            )

    async def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """Insert or replace records, running the sync chromadb call off-thread."""
        await asyncio.to_thread(
            self._collection.upsert,
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

    async def query(self, *, embedding: list[float], top_k: int) -> list[VectorMatch]:
        """Return up to ``top_k`` nearest matches, converting distance to similarity."""
        result = await asyncio.to_thread(
            self._collection.query,
            query_embeddings=[embedding],
            n_results=top_k,
        )
        return self._parse_query_result(result)

    @staticmethod
    def _parse_query_result(result: dict[str, Any]) -> list[VectorMatch]:
        """Flatten Chroma's per-query list-of-lists result into ``VectorMatch`` rows."""
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        matches: list[VectorMatch] = []
        for i, doc_id in enumerate(ids):
            matches.append(
                VectorMatch(
                    id=doc_id,
                    document=documents[i] if i < len(documents) else "",
                    score=_similarity_from_distance(
                        distances[i] if i < len(distances) else _MAX_COSINE_DISTANCE
                    ),
                    metadata=dict(metadatas[i]) if i < len(metadatas) and metadatas[i] else {},
                )
            )
        return matches

    async def delete_by_source(self, source: str) -> None:
        """Delete every record whose metadata ``source`` matches, off-thread."""
        await asyncio.to_thread(self._collection.delete, where={"source": source})

    async def aclose(self) -> None:
        """Chroma's persistent client flushes on write; satisfies the protocol."""
        return None
