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

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mangomas.rag.chunker import chunk_text
from mangomas.rag.loader import load_documents
from mangomas.telemetry import get_tracer

if TYPE_CHECKING:
    from mangomas.adapters.embeddings.base import EmbeddingClient
    from mangomas.adapters.vector.base import VectorStoreRepository
    from mangomas.config import RagSettings
    from mangomas.rag.loader import RawDoc

__all__ = ["IngestReport", "IngestionPipeline"]

logger = logging.getLogger(__name__)
_tracer = get_tracer(__name__)


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
        """Ingest the file or directory at ``path`` and return run counts.

        Instrumented deliberately: this is the longest-running operation in the
        product (a directory walk plus potentially thousands of embedding
        calls), and it is normally driven from the CLI, where a silent run and
        a broken run look identical.
        """
        with _tracer.start_as_current_span("rag.ingest") as span:
            span.set_attribute("rag.path", path)
            docs = await load_documents(path)
            span.set_attribute("rag.documents", len(docs))
            if not docs:
                # A path typo and an empty directory both land here; without
                # this the run reports documents=0 and gives no clue where it
                # looked.
                logger.warning(
                    "No documents found to ingest",
                    extra={"event": "rag_ingest_empty", "path": path},
                )
            else:
                logger.info(
                    "Ingestion started",
                    extra={"event": "rag_ingest_started", "path": path, "documents": len(docs)},
                )
            report = await self._ingest_documents(docs)
            span.set_attribute("rag.chunks", report.chunks)
            span.set_attribute("rag.batches", report.batches)
            span.set_attribute("rag.deleted_sources", report.deleted_sources)
            logger.info(
                "Ingestion finished",
                extra={
                    "event": "rag_ingest_finished",
                    "path": path,
                    "documents": report.documents,
                    "chunks": report.chunks,
                    "batches": report.batches,
                    "deleted_sources": report.deleted_sources,
                },
            )
            return report

    async def _ingest_documents(self, docs: list[RawDoc]) -> IngestReport:
        """Chunk, embed and upsert each already-loaded document."""
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
            logger.debug(
                "Prior vectors cleared",
                extra={"event": "rag_source_cleared", "source": doc.source, "removed": removed},
            )
            texts = chunk_text(
                doc.text,
                size=self._settings.chunk_words,
                overlap=self._settings.chunk_overlap,
                min_words=self._settings.min_chunk_words,
            )
            if not texts:
                # Every chunk fell under ``min_chunk_words``: the document is
                # silently dropped from the index. This is the "my file did
                # not get indexed and I have no idea why" case, so it warns.
                logger.warning(
                    "Document produced no chunks; skipped",
                    extra={
                        "event": "rag_document_skipped",
                        "source": doc.source,
                        "min_chunk_words": self._settings.min_chunk_words,
                    },
                )
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
            with _tracer.start_as_current_span("rag.embed_batch") as span:
                span.set_attribute("rag.source", source)
                span.set_attribute("rag.batch_size", len(batch))
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
            logger.debug(
                "Batch embedded and upserted",
                extra={
                    "event": "rag_batch_upserted",
                    "source": source,
                    "batch_index": batches,
                    "vectors": len(batch),
                },
            )
        return batches
