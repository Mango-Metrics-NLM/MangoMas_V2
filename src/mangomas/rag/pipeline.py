"""Ingestion pipeline: load → chunk → embed → upsert.

Drives an :class:`~mangomas.adapters.embeddings.base.EmbeddingClient` and a
:class:`~mangomas.adapters.vector.base.VectorStoreRepository` to turn raw
documents into persisted, queryable vectors.

Re-ingestion both *replaces* and *survives failure*.

**Replace.** Each document's prior vectors are deleted by source before the new
ones are written, so a document that has shrunk since the last run cannot leave
orphaned high-index chunks behind (stable ``{source}#{index}`` ids would
otherwise only overwrite the surviving prefix).

**Survive failure.** Every embedding call for a document completes *before* that
delete, so an embedding backend that is down, rate-limiting or timing out fails
the run without having touched the index. Re-running ``mangomas rag ingest``
during a provider outage is a no-op, not a deletion. Embeddings are still
computed in ``batch_size`` slices to bound request/payload size against the
backend; only the store writes are deferred.

The replace step itself is not atomic, and cannot be made so with the primitives
:class:`~mangomas.adapters.vector.base.VectorStoreRepository` exposes: there is
no transaction, and ``delete_by_source`` is the only deletion primitive — it
matches on the ``source`` metadata that the *new* vectors also carry, so moving
the delete after the upsert would erase what was just written. A failure of the
store between its own delete and the following upserts can therefore still lose
that source's vectors; re-ingesting the same path repairs it. Closing that
remaining window needs an id-targeted delete on the protocol.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from opentelemetry import trace

from mangomas.rag.chunker import chunk_text
from mangomas.rag.loader import load_documents

if TYPE_CHECKING:
    from mangomas.adapters.embeddings.base import EmbeddingClient
    from mangomas.adapters.vector.base import VectorStoreRepository
    from mangomas.config import RagSettings
    from mangomas.rag.loader import RawDoc

__all__ = ["IngestReport", "IngestionPipeline"]

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class _PreparedBatch:
    """One embedded slice of a document, ready to hand to the vector store.

    Internal. Exists so every embedding call can finish before the first index
    mutation: the pipeline accumulates prepared batches in memory (bounded by
    one document) rather than a half-written index.
    """

    ids: list[str]
    embeddings: list[list[float]]
    documents: list[str]
    metadatas: list[dict[str, Any]]


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
        with trace.get_tracer(__name__).start_as_current_span("rag.ingest") as span:
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
            texts = chunk_text(
                doc.text,
                size=self._settings.chunk_words,
                overlap=self._settings.chunk_overlap,
            )
            # Embed before mutating: until every vector for this document is in
            # hand the store is untouched, so an embedding backend that is down,
            # rate-limiting or timing out cannot leave the index emptier than it
            # started. ``texts == []`` needs no embedding at all and falls
            # straight through to the replace, which purges the now-empty
            # source — an emptied document must not keep its old passages.
            batches = await self._embed_document(doc.source, texts)
            removed = await self._replace_source(doc.source, batches)
            # Only sources the store actually held vectors for count as
            # deletions — a first-time ingest reports 0 (spec 0014 / D10).
            if removed > 0:
                deleted += 1
            if not texts:
                # ``chunk_text`` returns [] only for a document with no words
                # (empty or whitespace-only content). This is the "my file did
                # not get indexed and I have no idea why" case, so it warns.
                logger.warning(
                    "Document yielded no chunks (empty or whitespace-only content); skipped",
                    extra={
                        "event": "rag_document_skipped",
                        "source": doc.source,
                        "removed": removed,
                    },
                )
                continue
            total_chunks += len(texts)
            total_batches += len(batches)
        return IngestReport(
            documents=len(docs),
            chunks=total_chunks,
            batches=total_batches,
            deleted_sources=deleted,
        )

    async def _embed_document(self, source: str, texts: list[str]) -> list[_PreparedBatch]:
        """Embed ``texts`` in ``batch_size`` slices, touching no store state.

        This is the whole crash-safety mechanism: the vector store is not called
        from here, so whatever the embedding client raises propagates with the
        index exactly as it was. The caller only starts mutating once this
        returns a complete set of batches.
        """
        batches: list[_PreparedBatch] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            with trace.get_tracer(__name__).start_as_current_span("rag.embed_batch") as span:
                span.set_attribute("rag.source", source)
                span.set_attribute("rag.batch_size", len(batch))
                try:
                    embeddings = await self._embeddings.embed_batch(batch)
                except Exception:
                    # Re-raised: the run must fail. Logged first because the
                    # operator-relevant fact — that the index was left intact —
                    # is invisible in the traceback the CLI prints.
                    logger.exception(
                        "Embedding failed; index left unchanged for this source",
                        extra={
                            "event": "rag_source_embed_failed",
                            "source": source,
                            "chunks": len(texts),
                            "batches_completed": len(batches),
                        },
                    )
                    raise
            batches.append(
                _PreparedBatch(
                    ids=[f"{source}#{start + offset}" for offset in range(len(batch))],
                    embeddings=embeddings,
                    documents=batch,
                    metadatas=[
                        {"source": source, "index": start + offset} for offset in range(len(batch))
                    ],
                )
            )
        logger.debug(
            "Document embedded; index not yet modified",
            extra={
                "event": "rag_source_embedded",
                "source": source,
                "chunks": len(texts),
                "batches": len(batches),
            },
        )
        return batches

    async def _replace_source(self, source: str, batches: list[_PreparedBatch]) -> int:
        """Swap ``source``'s stored vectors for ``batches``; return the count removed.

        Delete first, then upsert. The ids are ``{source}#{index}``, so upserting
        a shrunk document over its predecessor would overwrite only the surviving
        prefix and leave orphaned high-index chunks; and ``delete_by_source``
        matches the ``source`` metadata the new vectors also carry, so it cannot
        run afterwards without erasing them. An empty ``batches`` is a purge,
        which is the correct outcome for a document that has become empty.
        """
        removed = await self._vector_store.delete_by_source(source)
        upserted = 0
        for batch_index, prepared in enumerate(batches, start=1):
            await self._vector_store.upsert(
                ids=prepared.ids,
                embeddings=prepared.embeddings,
                documents=prepared.documents,
                metadatas=prepared.metadatas,
            )
            upserted += len(prepared.ids)
            logger.debug(
                "Batch upserted",
                extra={
                    "event": "rag_batch_upserted",
                    "source": source,
                    "batch_index": batch_index,
                    "vectors": len(prepared.ids),
                },
            )
        # The per-source ledger an operator needs to answer "what did that run
        # actually do to my index?" — one line, both halves of the swap.
        logger.info(
            "Source replaced",
            extra={
                "event": "rag_source_replaced",
                "source": source,
                "removed": removed,
                "upserted": upserted,
                "batches": len(batches),
            },
        )
        return removed
