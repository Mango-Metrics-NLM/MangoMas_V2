"""Gated end-to-end RAG test: real sentence-transformers + ephemeral Chroma.

Skipped unless BOTH ``RUN_EMBEDDINGS_LOCAL=1`` and ``RUN_RAG=1`` are set (the
``embeddings_local`` and ``rag`` markers each gate on their env var), because it
requires the ``embeddings-local`` and ``rag`` extras to be installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mangomas.config import RagSettings
from mangomas.rag import IngestionPipeline, Retriever

pytestmark = [pytest.mark.embeddings_local, pytest.mark.rag]


async def test_ingest_then_query_round_trip(tmp_path: Path) -> None:
    from mangomas.adapters.embeddings.sentence_transformers import (  # noqa: PLC0415
        SentenceTransformersEmbeddingClient,
    )
    from mangomas.adapters.vector.chroma import ChromaVectorStore  # noqa: PLC0415

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "harness.md").write_text(
        "The harness wraps agent dispatch in a telemetry span for observability.",
        encoding="utf-8",
    )
    (corpus / "loop.md").write_text(
        "The orchestrator loop caps steps and enforces a per-step timeout.",
        encoding="utf-8",
    )

    embeddings = SentenceTransformersEmbeddingClient(model="all-MiniLM-L6-v2")
    store = ChromaVectorStore(
        persist_dir=str(tmp_path / "chroma"),
        collection_name="e2e",
    )
    try:
        pipeline = IngestionPipeline(
            embeddings=embeddings,
            vector_store=store,
            settings=RagSettings(chunk_words=50, chunk_overlap=10, min_chunk_words=1),
            batch_size=8,
        )
        report = await pipeline.ingest(str(corpus))
        assert report.documents == 2
        assert report.chunks >= 2

        retriever = Retriever(embeddings=embeddings, vector_store=store, top_k=2)
        results = await retriever.search("how does the harness work?")
        assert results
        assert "harness" in results[0].chunk.text.lower()
    finally:
        await embeddings.aclose()
        await store.aclose()
