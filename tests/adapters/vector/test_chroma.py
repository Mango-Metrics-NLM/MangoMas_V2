"""Tests for ChromaVectorStore via an injected fake collection (no chromadb dep)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from mangomas.adapters.vector.chroma import ChromaVectorStore, _similarity_from_distance
from tests.constants import TEST_VECTOR_COLLECTION, TEST_VECTOR_PERSIST_DIR


@dataclass
class _FakeCollection:
    """Mimics the slice of the chromadb Collection API the adapter touches."""

    query_result: dict[str, Any] = field(default_factory=dict)
    upserts: list[dict[str, Any]] = field(default_factory=list)
    queries: list[dict[str, Any]] = field(default_factory=list)
    deletes: list[dict[str, Any]] = field(default_factory=list)

    def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        self.upserts.append(
            {"ids": ids, "embeddings": embeddings, "documents": documents, "metadatas": metadatas}
        )

    def query(self, *, query_embeddings: list[list[float]], n_results: int) -> dict[str, Any]:
        self.queries.append({"query_embeddings": query_embeddings, "n_results": n_results})
        return self.query_result

    def delete(self, *, where: dict[str, Any]) -> None:
        self.deletes.append({"where": where})


def _store(collection: _FakeCollection) -> ChromaVectorStore:
    return ChromaVectorStore(
        persist_dir=TEST_VECTOR_PERSIST_DIR,
        collection_name=TEST_VECTOR_COLLECTION,
        collection=collection,
    )


async def test_upsert_forwards_all_lists() -> None:
    col = _FakeCollection()
    store = _store(col)
    await store.upsert(
        ids=["a#0"],
        embeddings=[[0.1, 0.2]],
        documents=["hello"],
        metadatas=[{"source": "a"}],
    )
    assert col.upserts == [
        {
            "ids": ["a#0"],
            "embeddings": [[0.1, 0.2]],
            "documents": ["hello"],
            "metadatas": [{"source": "a"}],
        }
    ]


async def test_query_maps_distance_to_similarity() -> None:
    col = _FakeCollection(
        query_result={
            "ids": [["a#0", "a#1"]],
            "documents": [["doc0", "doc1"]],
            "distances": [[0.0, 2.0]],
            "metadatas": [[{"source": "a"}, {"source": "a"}]],
        }
    )
    store = _store(col)
    matches = await store.query(embedding=[0.1, 0.2], top_k=2)
    assert col.queries[0]["n_results"] == 2
    assert [m.id for m in matches] == ["a#0", "a#1"]
    # distance 0 → similarity 1.0; distance 2 → similarity 0.0.
    assert matches[0].score == pytest.approx(1.0)
    assert matches[1].score == pytest.approx(0.0)
    assert matches[0].metadata == {"source": "a"}


async def test_query_handles_empty_result() -> None:
    col = _FakeCollection(query_result={})
    store = _store(col)
    assert await store.query(embedding=[0.1], top_k=5) == []


async def test_query_tolerates_missing_metadata_rows() -> None:
    col = _FakeCollection(
        query_result={
            "ids": [["a#0"]],
            "documents": [["doc0"]],
            "distances": [[1.0]],
            "metadatas": [[None]],
        }
    )
    store = _store(col)
    matches = await store.query(embedding=[0.1], top_k=1)
    assert matches[0].metadata == {}
    assert matches[0].score == pytest.approx(0.5)


async def test_delete_by_source_targets_metadata() -> None:
    col = _FakeCollection()
    store = _store(col)
    await store.delete_by_source("a")
    assert col.deletes == [{"where": {"source": "a"}}]


async def test_aclose_is_noop() -> None:
    col = _FakeCollection()
    store = _store(col)
    await store.aclose()  # must not raise


def test_similarity_clamps_to_unit_interval() -> None:
    # Negative cosine distances (FP noise) clamp to 1.0; > 2 clamps to 0.0.
    assert _similarity_from_distance(-0.001) == 1.0
    assert _similarity_from_distance(2.5) == 0.0
    assert _similarity_from_distance(1.0) == pytest.approx(0.5)
