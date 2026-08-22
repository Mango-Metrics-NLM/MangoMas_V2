"""Tests for the RAG ingestion pipeline (fakes only — no chromadb / network)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from mangomas.config import RagSettings
from mangomas.rag.pipeline import IngestionPipeline, IngestReport
from tests.fakes import FakeEmbeddingClient, FakeVectorStore

# size=3, overlap=0, min_words=1 → one chunk per 3 words, no fragment dropping.
_SETTINGS = RagSettings(chunk_words=3, chunk_overlap=0, min_chunk_words=1)


def _pipeline(
    embeddings: FakeEmbeddingClient,
    vector_store: FakeVectorStore,
    *,
    batch_size: int,
) -> IngestionPipeline:
    return IngestionPipeline(
        embeddings=embeddings,
        vector_store=vector_store,
        settings=_SETTINGS,
        batch_size=batch_size,
    )


async def test_ingest_chunks_embeds_and_upserts(tmp_path: Path) -> None:
    f = tmp_path / "doc.txt"
    f.write_text("one two three four five six", encoding="utf-8")  # 6 words → 2 chunks
    emb = FakeEmbeddingClient()
    store = FakeVectorStore()
    report = _pipeline(emb, store, batch_size=10).ingest
    result = await report(str(f))

    # First-time ingest: the store held nothing for this source → 0 deletions.
    assert result == IngestReport(documents=1, chunks=2, batches=1, deleted_sources=0)
    # Stable {source}#{index} ids.
    assert sorted(store.records) == [f"{f.as_posix()}#0", f"{f.as_posix()}#1"]
    assert store.records[f"{f.as_posix()}#0"]["document"] == "one two three"
    assert store.records[f"{f.as_posix()}#1"]["document"] == "four five six"
    # source written into metadata so deletes can target it.
    assert store.records[f"{f.as_posix()}#0"]["metadata"]["source"] == f.as_posix()


async def test_ingest_slices_into_batches(tmp_path: Path) -> None:
    f = tmp_path / "doc.txt"
    # 9 words → 3 chunks; batch_size=2 → 2 batches (2 + 1).
    f.write_text("a b c d e f g h i", encoding="utf-8")
    emb = FakeEmbeddingClient()
    store = FakeVectorStore()
    result = await _pipeline(emb, store, batch_size=2).ingest(str(f))

    assert result.chunks == 3
    assert result.batches == 2
    # embed_batch called once per batch with the right slice sizes.
    assert [len(c) for c in emb.calls] == [2, 1]


async def test_ingest_deletes_source_before_upsert(tmp_path: Path) -> None:
    f = tmp_path / "doc.txt"
    f.write_text("one two three", encoding="utf-8")
    emb = FakeEmbeddingClient()
    store = FakeVectorStore()
    await _pipeline(emb, store, batch_size=10).ingest(str(f))
    assert store.deleted_sources == [f.as_posix()]


async def test_ingest_empty_document_upserts_nothing_but_still_deletes(tmp_path: Path) -> None:
    f = tmp_path / "empty.txt"
    f.write_text("   \n  ", encoding="utf-8")  # whitespace only → no chunks
    emb = FakeEmbeddingClient()
    store = FakeVectorStore()
    result = await _pipeline(emb, store, batch_size=10).ingest(str(f))

    assert result == IngestReport(documents=1, chunks=0, batches=0, deleted_sources=0)
    assert store.records == {}
    assert store.deleted_sources == [f.as_posix()]  # idempotent cleanup still runs


async def test_ingest_directory_processes_each_doc(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("one two three", encoding="utf-8")
    (tmp_path / "b.md").write_text("four five six", encoding="utf-8")
    emb = FakeEmbeddingClient()
    store = FakeVectorStore()
    result = await _pipeline(emb, store, batch_size=10).ingest(str(tmp_path))

    assert result.documents == 2
    assert result.chunks == 2
    assert sorted(store.records) == ["a.txt#0", "b.md#0"]


async def test_deleted_sources_counts_real_deletions_only(tmp_path: Path) -> None:
    """Regression (spec 0014 / D10): ``deleted_sources`` used to equal
    ``documents`` unconditionally; it must count only sources whose prior
    vectors were actually removed."""
    f = tmp_path / "doc.txt"
    f.write_text("one two three four five six", encoding="utf-8")  # 6 words → 2 chunks
    emb = FakeEmbeddingClient()
    store = FakeVectorStore()

    first = await _pipeline(emb, store, batch_size=10).ingest(str(f))
    assert first.documents == 1
    assert first.deleted_sources == 0  # brand-new source: nothing to delete

    second = await _pipeline(emb, store, batch_size=10).ingest(str(f))
    assert second.documents == 1
    assert second.deleted_sources == 1  # re-ingest: prior chunks were removed


async def test_batch_size_floored_at_one(tmp_path: Path) -> None:
    f = tmp_path / "doc.txt"
    f.write_text("one two three four five six", encoding="utf-8")  # 2 chunks
    emb = FakeEmbeddingClient()
    store = FakeVectorStore()
    result = await _pipeline(emb, store, batch_size=0).ingest(str(f))
    # batch_size 0 is clamped to 1 → one batch per chunk.
    assert result.batches == 2
    assert [len(c) for c in emb.calls] == [1, 1]


# ── Observability of the silent-failure paths (spec-0023 R2) ──────────────────
#
# Both assert on the structured ``extra=`` fields via ``caplog.records`` rather
# than ``caplog.text``: the default formatter renders only the message, so a
# text assertion would pass even if every structured field were dropped —
# which is exactly the regression these guard.


def _events(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [getattr(r, "event", "") for r in caplog.records]


async def test_document_producing_no_chunks_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A document that yields no chunks must warn, not vanish silently.

    This is the "my file did not get indexed and I have no idea why" case: the
    pipeline skips the document, the report still counts it under
    ``documents``, and before this there was no output at any level.

    The message must name the real cause — the document yielded no chunks
    because it had no words (empty or whitespace-only content). It used to
    misattribute the skip to ``min_chunk_words``, which is provably inert
    (pinned by ``test_fuzz_min_words_never_changes_the_output``).
    """
    f = tmp_path / "empty.txt"
    f.write_text("   \n  ", encoding="utf-8")
    pipeline = _pipeline(FakeEmbeddingClient(), FakeVectorStore(), batch_size=2)

    with caplog.at_level(logging.WARNING, logger="mangomas.rag.pipeline"):
        report = await pipeline.ingest(str(f))

    assert report.documents == 1
    assert report.chunks == 0
    assert "rag_document_skipped" in _events(caplog)
    skipped = next(r for r in caplog.records if getattr(r, "event", "") == "rag_document_skipped")
    # getattr: LogRecord has no static schema for `extra=` fields.
    assert getattr(skipped, "source", None) == str(f)
    # The message names the real cause, not the inert min_chunk_words knob.
    assert "empty or whitespace-only" in skipped.getMessage()
    assert "min_chunk_words" not in skipped.getMessage()
    assert not hasattr(skipped, "min_chunk_words")


async def test_empty_path_warns_rather_than_reporting_zero_silently(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An empty directory (or a path typo) must say so, not just report 0."""
    pipeline = _pipeline(FakeEmbeddingClient(), FakeVectorStore(), batch_size=2)

    with caplog.at_level(logging.WARNING, logger="mangomas.rag.pipeline"):
        report = await pipeline.ingest(str(tmp_path))

    assert report == IngestReport(documents=0, chunks=0, batches=0, deleted_sources=0)
    assert "rag_ingest_empty" in _events(caplog)
