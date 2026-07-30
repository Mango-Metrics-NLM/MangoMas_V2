"""Ingestion pipeline: load → chunk → embed → upsert.

Drives an :class:`~mangomas.adapters.embeddings.base.EmbeddingClient` and a
:class:`~mangomas.adapters.vector.base.VectorStoreRepository` to turn raw
documents into persisted, queryable vectors.

Re-ingestion is idempotent: each document is *first* deleted by source, so a
document that has shrunk since the last run cannot leave orphaned high-index
chunks behind (stable ``{source}#{index}`` ids would otherwise only overwrite
the surviving prefix). Embeddings are computed in ``batch_size`` slices to bound
request/payload size against the embedding backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from mangomas.rag.chunker import chunk_text
from mangomas.rag.loader import load_documents

if TYPE_CHECKING:
    from mangomas.adapters.embeddings.base import EmbeddingClient
    from mangomas.adapters.vector.base import VectorStoreRepository
    from mangomas.config import RagSettings

__all__ = ["IngestReport", "IngestionPipeline"]


@dataclass(frozen=True)
class IngestReport:
    """Counts summarising a single :meth:`IngestionPipeline.ingest` run.

    ``deleted_sources`` counts only sources whose prior vectors were actually
    removed before re-upserting — a first-time ingest reports ``0``.
    """

    documents: int
    chunks: int
    batches: int
    deleted_sources: int


class IngestionPipeline:
    """Coordinates loading, chunking, embedding and upserting of documents."""

    def __init__(
        self,
        *,
        embeddings: EmbeddingClient,
        vector_store: VectorStoreRepository,
        settings: RagSettings,
        batch_size: int,
    ) -> None:
        self._embeddings = embeddings
        self._vector_store = vector_store
        self._settings = settings
        self._batch_size = max(1, batch_size)

    async def ingest(self, path: str) -> IngestReport:
        """Ingest the file or directory at ``path`` and return run counts."""
        docs = await load_documents(path)
        total_chunks = 0
        total_batches = 0
        deleted = 0
        for doc in docs:
            # Idempotent re-ingest: clear prior chunks for this source first.
            # Only sources the store actually held vectors for count as
            # deletions — a first-time ingest reports 0 (spec 0014 / D10).
            removed = await self._vector_store.delete_by_source(doc.source)
            if removed > 0:
                deleted += 1
            texts = chunk_text(
                doc.text,
                size=self._settings.chunk_words,
                overlap=self._settings.chunk_overlap,
                min_words=self._settings.min_chunk_words,
            )
            if not texts:
                continue
            total_chunks += len(texts)
            total_batches += await self._embed_and_upsert(doc.source, texts)
        return IngestReport(
            documents=len(docs),
            chunks=total_chunks,
            batches=total_batches,
            deleted_sources=deleted,
        )

    async def _embed_and_upsert(self, source: str, texts: list[str]) -> int:
        """Embed ``texts`` in batches and upsert them; return the batch count."""
        batches = 0
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            embeddings = await self._embeddings.embed_batch(batch)
            ids = [f"{source}#{start + offset}" for offset in range(len(batch))]
            metadatas = [
                {"source": source, "index": start + offset} for offset in range(len(batch))
            ]
            await self._vector_store.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=batch,
                metadatas=metadatas,
            )
            batches += 1
        return batches
