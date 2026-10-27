"""Tests for ChromaVectorStore via an injected fake collection (no chromadb dep)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pytest

from mangomas.adapters.vector.chroma import ChromaVectorStore, _similarity_from_distance
from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE
from mangomas.errors import PersistenceError
from tests.constants import TEST_VECTOR_COLLECTION, TEST_VECTOR_PERSIST_DIR


@dataclass
class _FakeCollection:
    """Mimics the slice of the chromadb Collection API the adapter touches."""

    query_result: dict[str, Any] = field(default_factory=dict)
    get_result: dict[str, Any] = field(default_factory=dict)
    upserts: list[dict[str, Any]] = field(default_factory=list)
    queries: list[dict[str, Any]] = field(default_factory=list)
    gets: list[dict[str, Any]] = field(default_factory=list)
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

    def get(self, *, where: dict[str, Any], include: list[str]) -> dict[str, Any]:
        self.gets.append({"where": where, "include": include})
        return self.get_result

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
    col = _FakeCollection(get_result={"ids": ["a#0", "a#1"]})
    store = _store(col)
    removed = await store.delete_by_source("a")
    # ids counted first (ids-only fetch), then the metadata-targeted delete.
    assert removed == 2
    assert col.gets == [{"where": {"source": "a"}, "include": []}]
    assert col.deletes == [{"where": {"source": "a"}}]


async def test_delete_by_source_returns_zero_for_unknown_source() -> None:
    col = _FakeCollection()  # empty get_result → no ids for this source
    store = _store(col)
    assert await store.delete_by_source("ghost") == 0
    assert col.deletes == [{"where": {"source": "ghost"}}]


async def test_aclose_is_noop() -> None:
    col = _FakeCollection()
    store = _store(col)
    await store.aclose()  # must not raise


def test_similarity_clamps_to_unit_interval() -> None:
    # Negative cosine distances (FP noise) clamp to 1.0; > 2 clamps to 0.0.
    assert _similarity_from_distance(-0.001) == 1.0
    assert _similarity_from_distance(2.5) == 0.0
    assert _similarity_from_distance(1.0) == pytest.approx(0.5)


# ── Typed errors at the boundary ─────────────────────────────────────────────
# chromadb raises its own exception classes; none is a MangomasError, so a raw
# one would reach the API as an untyped 500. Mutation (proved while writing):
# removing any one ``except`` in the adapter makes the matching test below
# fail with the raw ``RuntimeError``.


@dataclass
class _RaisingCollection(_FakeCollection):
    """Every call fails the way a corrupt or locked Chroma collection would."""

    error: Exception = field(default_factory=lambda: RuntimeError("hnsw index corrupt"))

    def upsert(self, **_: Any) -> None:
        raise self.error

    def query(self, **_: Any) -> dict[str, Any]:
        raise self.error

    def get(self, **_: Any) -> dict[str, Any]:
        raise self.error


async def test_upsert_failure_is_a_typed_persistence_error() -> None:
    store = _store(_RaisingCollection())
    with pytest.raises(PersistenceError) as excinfo:
        await store.upsert(ids=["a"], embeddings=[[1.0]], documents=["d"], metadatas=[{}])
    assert str(excinfo.value) == "Vector store upsert failed"
    assert excinfo.value.detail == "RuntimeError: hnsw index corrupt"


async def test_query_failure_is_a_typed_persistence_error() -> None:
    store = _store(_RaisingCollection())
    with pytest.raises(PersistenceError) as excinfo:
        await store.query(embedding=[1.0], top_k=1)
    assert str(excinfo.value) == "Vector store query failed"


async def test_delete_failure_is_a_typed_persistence_error() -> None:
    store = _store(_RaisingCollection())
    with pytest.raises(PersistenceError) as excinfo:
        await store.delete_by_source("doc.md")
    assert str(excinfo.value) == "Vector store delete failed"


async def test_failure_detail_is_bounded() -> None:
    huge = _RaisingCollection(error=RuntimeError("x" * (DEFAULT_ERROR_DETAIL_TRUNCATE * 3)))
    with pytest.raises(PersistenceError) as excinfo:
        await _store(huge).query(embedding=[1.0], top_k=1)
    assert len(excinfo.value.detail) <= DEFAULT_ERROR_DETAIL_TRUNCATE


async def test_failure_is_logged_with_operation_and_error_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="mangomas.adapters.vector.chroma")
    with pytest.raises(PersistenceError):
        await _store(_RaisingCollection()).upsert(
            ids=["a"], embeddings=[[1.0]], documents=["d"], metadatas=[{}]
        )
    record = next(r for r in caplog.records if r.getMessage() == "Chroma upsert failed")
    assert record.operation == "upsert"  # type: ignore[attr-defined]
    assert record.error_type == "RuntimeError"  # type: ignore[attr-defined]
