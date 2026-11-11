"""Tests for Vertex AI embedding dimension isolation and Chroma mismatch guards.

Verifies that:
1. Incompatible embedding dimensions (e.g., 384-dim local vs. 768-dim Vertex AI)
   raise typed ``PersistenceError`` when written or queried against the same Chroma collection.
2. Isolated collection conventions (``mangomas_vertex`` / ``./data/chroma_vertex``)
   allow local and cloud embedding models to coexist cleanly without collision.
3. ``IngestionPipeline`` and ``Retriever`` correctly surface vector store errors
   when dimensional mismatch occurs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from mangomas.adapters.embeddings.vertex import VertexEmbeddingClient
from mangomas.adapters.vector.chroma import ChromaVectorStore
from mangomas.config import RagSettings
from mangomas.errors import PersistenceError
from mangomas.rag import IngestionPipeline, Retriever

# Every case opens a real ChromaVectorStore, which needs the `rag` extra
# (chromadb); `make install` installs only `[dev]`. The marker pair mirrors the
# other chromadb-backed suites in tests/rag/: both gates must be opened, which
# `make embeddings-local` does on the nightly job that installs
# `[dev,embeddings-local,rag]` (.github/workflows/nightly.yml). The CI-safe
# `make rag` lane stays fakes-only.
pytestmark = [pytest.mark.embeddings_local, pytest.mark.rag]

# Vector dimension constants. 768 is text-embedding-004's fixed output size;
# gemini-embedding-001 defaults to 3072 unless output_dimensionality is set,
# which is exactly the silent switch the isolation convention guards against.
LOCAL_EMBEDDING_DIM = 384
VERTEX_EMBEDDING_DIM = 768


@dataclass
class _FakeEmbedding:
    values: list[float]


@dataclass
class _FakeModelClient:
    """Fake Vertex TextEmbeddingModel returning fixed-dimension vectors."""

    dim: int = VERTEX_EMBEDDING_DIM

    async def get_embeddings_async(self, texts: list[str]) -> list[_FakeEmbedding]:
        return [_FakeEmbedding(values=[0.05] * self.dim) for _ in texts]


def _make_store(persist_dir: Path, collection_name: str) -> ChromaVectorStore:
    return ChromaVectorStore(
        persist_dir=str(persist_dir),
        collection_name=collection_name,
    )


async def test_chroma_upsert_dimension_mismatch_raises_persistence_error(
    tmp_path: Path,
) -> None:
    """Upserting 768-dim vectors into a 384-dim Chroma collection must fail closed."""
    store = _make_store(tmp_path / "chroma_collision", "test_collection")

    # Initialise collection with 384 dimensions
    await store.upsert(
        ids=["doc#0"],
        embeddings=[[0.1] * LOCAL_EMBEDDING_DIM],
        documents=["local passage"],
        metadatas=[{"source": "local"}],
    )

    # Attempt to write 768-dimensional Vertex AI vector into the same collection
    with pytest.raises(PersistenceError) as excinfo:
        await store.upsert(
            ids=["doc#1"],
            embeddings=[[0.2] * VERTEX_EMBEDDING_DIM],
            documents=["vertex passage"],
            metadatas=[{"source": "vertex"}],
        )

    assert "Vector store upsert failed" in str(excinfo.value)
    assert excinfo.value.__cause__ is not None


async def test_chroma_query_dimension_mismatch_raises_persistence_error(
    tmp_path: Path,
) -> None:
    """Querying a 384-dim collection with a 768-dim vector must raise PersistenceError."""
    store = _make_store(tmp_path / "chroma_query_collision", "test_collection")

    # Initialise collection with 384 dimensions
    await store.upsert(
        ids=["doc#0"],
        embeddings=[[0.1] * LOCAL_EMBEDDING_DIM],
        documents=["local passage"],
        metadatas=[{"source": "local"}],
    )

    # Attempt to query with a 768-dimensional vector
    with pytest.raises(PersistenceError) as excinfo:
        await store.query(embedding=[0.2] * VERTEX_EMBEDDING_DIM, top_k=1)

    assert "Vector store query failed" in str(excinfo.value)
    assert excinfo.value.__cause__ is not None


async def test_reverse_dimension_mismatch_raises_persistence_error(
    tmp_path: Path,
) -> None:
    """Upserting 384-dim vectors into a 768-dim Chroma collection must fail closed."""
    store = _make_store(tmp_path / "chroma_rev_collision", "test_vertex_col")

    # Initialise collection with 768 dimensions
    await store.upsert(
        ids=["vdoc#0"],
        embeddings=[[0.1] * VERTEX_EMBEDDING_DIM],
        documents=["vertex passage"],
        metadatas=[{"source": "vertex"}],
    )

    # Attempt to write 384-dimensional vector into the 768-dim collection
    with pytest.raises(PersistenceError) as excinfo:
        await store.upsert(
            ids=["vdoc#1"],
            embeddings=[[0.2] * LOCAL_EMBEDDING_DIM],
            documents=["local passage"],
            metadatas=[{"source": "local"}],
        )

    assert "Vector store upsert failed" in str(excinfo.value)


async def test_isolated_collections_allow_distinct_dimensions(tmp_path: Path) -> None:
    """Separate persist dirs and collection names permit 384 and 768 dim coexistence."""
    local_store = _make_store(tmp_path / "chroma_local", "mangomas")
    vertex_store = _make_store(tmp_path / "chroma_vertex", "mangomas_vertex")

    # Local store handles 384-dim vectors
    await local_store.upsert(
        ids=["local#0"],
        embeddings=[[0.1] * LOCAL_EMBEDDING_DIM],
        documents=["local passage"],
        metadatas=[{"source": "local"}],
    )

    # Vertex store handles 768-dim vectors
    await vertex_store.upsert(
        ids=["vertex#0"],
        embeddings=[[0.2] * VERTEX_EMBEDDING_DIM],
        documents=["vertex passage"],
        metadatas=[{"source": "vertex"}],
    )

    # Both stores query successfully at their respective dimensions
    local_matches = await local_store.query(embedding=[0.1] * LOCAL_EMBEDDING_DIM, top_k=1)
    assert len(local_matches) == 1
    assert local_matches[0].id == "local#0"

    vertex_matches = await vertex_store.query(embedding=[0.2] * VERTEX_EMBEDDING_DIM, top_k=1)
    assert len(vertex_matches) == 1
    assert vertex_matches[0].id == "vertex#0"


async def test_ingestion_pipeline_with_vertex_embeddings_isolated(
    tmp_path: Path,
) -> None:
    """IngestionPipeline successfully embeds and stores 768-dim chunks in isolated store."""
    doc_path = tmp_path / "sample.md"
    doc_path.write_text("Mango-Mas provides modular multi-agent support for Google Cloud.")

    fake_model = _FakeModelClient(dim=VERTEX_EMBEDDING_DIM)
    embedding_client = VertexEmbeddingClient(
        project_id="test-project",
        model="text-embedding-004",
        client=fake_model,
    )
    store = _make_store(tmp_path / "chroma_vertex", "mangomas_vertex")

    pipeline = IngestionPipeline(
        embeddings=embedding_client,
        vector_store=store,
        settings=RagSettings(chunk_words=50, chunk_overlap=10),
        batch_size=8,
    )

    report = await pipeline.ingest(str(doc_path))
    assert report.documents == 1
    assert report.chunks >= 1
    assert report.deleted_sources == 0

    # Verify query through Retriever
    retriever = Retriever(embeddings=embedding_client, vector_store=store, top_k=1)
    results = await retriever.search("Google Cloud", top_k=1)
    assert len(results) == 1
    assert "Mango-Mas" in results[0].chunk.text
    assert 0.0 <= results[0].score <= 1.0


async def test_ingestion_pipeline_collision_raises_persistence_error(
    tmp_path: Path,
) -> None:
    """IngestionPipeline raises PersistenceError if pointed at an incompatible collection."""
    # Seed collection with 384 dimensions
    store = _make_store(tmp_path / "chroma_shared", "mangomas")
    await store.upsert(
        ids=["seed#0"],
        embeddings=[[0.1] * LOCAL_EMBEDDING_DIM],
        documents=["seed"],
        metadatas=[{"source": "seed"}],
    )

    # Ingest 768-dim doc via Vertex client into the 384-dim collection
    doc_path = tmp_path / "sample.md"
    doc_path.write_text("This should collide and raise PersistenceError.")

    fake_model = _FakeModelClient(dim=VERTEX_EMBEDDING_DIM)
    embedding_client = VertexEmbeddingClient(
        project_id="test-project",
        model="text-embedding-004",
        client=fake_model,
    )

    pipeline = IngestionPipeline(
        embeddings=embedding_client,
        vector_store=store,
        settings=RagSettings(chunk_words=50, chunk_overlap=10),
        batch_size=8,
    )

    with pytest.raises(PersistenceError):
        await pipeline.ingest(str(doc_path))
