"""Tests for the RAG ingestion pipeline (fakes only — no chromadb / network)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from mangomas.config import RagSettings
from mangomas.errors import LLMUnavailable
from mangomas.rag.pipeline import IngestionPipeline, IngestReport
from tests.fakes import FakeEmbeddingClient, FakeVectorStore

# size=3, overlap=0 → one chunk per 3 words.
_SETTINGS = RagSettings(chunk_words=3, chunk_overlap=0)

# Sentinel message for an injected embedding-provider outage. Local to this
# module rather than `tests.constants`: it is a fixture sentinel, not a domain
# value (no URL, model id, env-var name, limit or roster).
_OUTAGE = "embedding backend unavailable"


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
    because it had no words (empty or whitespace-only content).
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
    assert getattr(skipped, "source", None) == f.as_posix()
    # The message names the real cause, not a retired drop-threshold knob.
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


async def test_ingest_skips_binary_files(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A binary file should be skipped gracefully."""
    f = tmp_path / "binary.bin"
    f.write_bytes(b"\xff\xfe\x00\x01\x80\xff")
    pipeline = _pipeline(FakeEmbeddingClient(), FakeVectorStore(), batch_size=2)

    with caplog.at_level(logging.WARNING, logger="mangomas.rag.pipeline"):
        report = await pipeline.ingest(str(f))

    # The pipeline should skip binary files (UnicodeDecodeError handled).
    # Might count as documents=1 if it tries, but chunks=0.
    # Let's assert it doesn't crash and chunks=0.
    assert report.chunks == 0


# ── Crash-safety: an ingest must never leave the index emptier than it started ─
#
# The pipeline has no transaction. Before this, each document was processed as
# delete_by_source → chunk → embed_batch → upsert, so an embedding provider that
# was down, rate-limiting or timing out *after* the delete destroyed that
# source's vectors with nothing to replace them: re-running `mangomas rag ingest`
# during an LLM outage was a destructive operation on the index.
#
# The fix moves every embedding call ahead of the first index mutation, so the
# store is untouched until all vectors for a document are in hand. These guards
# assert on the store's contents, not on call ordering, because the contract is
# about surviving data, not about a sequence.


async def _ingest_once(tmp_path: Path, text: str) -> tuple[Path, FakeVectorStore]:
    """Populate a store with one document; return the path and the store."""
    f = tmp_path / "doc.txt"
    f.write_text(text, encoding="utf-8")
    store = FakeVectorStore()
    await _pipeline(FakeEmbeddingClient(), store, batch_size=10).ingest(str(f))
    return f, store


async def test_embedding_failure_leaves_prior_vectors_intact(tmp_path: Path) -> None:
    """A provider outage during re-ingest must not empty the source's vectors."""
    f, store = await _ingest_once(tmp_path, "one two three four five six")
    before = {doc_id: dict(rec) for doc_id, rec in store.records.items()}
    assert before, "fixture must leave vectors to lose"

    down = FakeEmbeddingClient(raise_on_batch=LLMUnavailable(_OUTAGE))
    with pytest.raises(LLMUnavailable):
        await _pipeline(down, store, batch_size=10).ingest(str(f))

    # The searchable index is exactly what it was: the failed run is a no-op,
    # not a deletion.
    assert store.records == before


async def test_embedding_failure_mid_document_leaves_prior_vectors_intact(
    tmp_path: Path,
) -> None:
    """Deferring the delete must cover a *late* batch failure too.

    Failing only the first batch would also pass if the delete were merely moved
    after the first successful embed. Here batch 1 succeeds and batch 2 raises,
    which discriminates "delete after the first embed" from "delete after every
    embed".
    """
    f, store = await _ingest_once(tmp_path, "a b c d e f g h i")  # 3 chunks
    before = {doc_id: dict(rec) for doc_id, rec in store.records.items()}
    assert len(before) == 3

    down = FakeEmbeddingClient(raise_on_batch=LLMUnavailable(_OUTAGE), raise_after_batches=1)
    with pytest.raises(LLMUnavailable):
        await _pipeline(down, store, batch_size=2).ingest(str(f))

    assert len(down.calls) == 2, "the second batch must actually have been attempted"
    assert store.records == before


async def test_embedding_failure_does_not_delete_at_all(tmp_path: Path) -> None:
    """The store is never asked to delete when the embeddings never arrived.

    Stronger than the records assertion above: it pins that the failed run left
    no trace on the store at all, so a store whose delete is asynchronous or
    lazily flushed cannot smuggle the loss through.
    """
    f, store = await _ingest_once(tmp_path, "one two three")
    store.deleted_sources.clear()
    store.upserts.clear()

    down = FakeEmbeddingClient(raise_on_batch=LLMUnavailable(_OUTAGE))
    with pytest.raises(LLMUnavailable):
        await _pipeline(down, store, batch_size=10).ingest(str(f))

    assert store.deleted_sources == []
    assert store.upserts == []


async def test_failed_document_does_not_destroy_a_sibling(tmp_path: Path) -> None:
    """One failing document must not take the whole directory's index with it."""
    (tmp_path / "a.txt").write_text("one two three", encoding="utf-8")
    (tmp_path / "b.md").write_text("four five six", encoding="utf-8")
    store = FakeVectorStore()
    await _pipeline(FakeEmbeddingClient(), store, batch_size=10).ingest(str(tmp_path))
    before = {doc_id: dict(rec) for doc_id, rec in store.records.items()}
    assert sorted(before) == ["a.txt#0", "b.md#0"]

    # Fail on the *first* document, so the run aborts before b.md is reached.
    down = FakeEmbeddingClient(raise_on_batch=LLMUnavailable(_OUTAGE))
    with pytest.raises(LLMUnavailable):
        await _pipeline(down, store, batch_size=10).ingest(str(tmp_path))

    assert store.records == before


async def test_deleted_sources_still_counts_real_deletions_after_reorder(
    tmp_path: Path,
) -> None:
    """``deleted_sources`` stays truthful once the delete moves after embedding.

    Pins both directions of the counter the earlier fix (spec 0014 / D10)
    established, now that the delete no longer runs first: 0 when the store held
    nothing, 1 when prior vectors really were removed, and — the new case — 0
    for a run that aborted before touching the store.
    """
    f = tmp_path / "doc.txt"
    f.write_text("one two three four five six", encoding="utf-8")
    store = FakeVectorStore()

    first = await _pipeline(FakeEmbeddingClient(), store, batch_size=10).ingest(str(f))
    assert first.deleted_sources == 0

    down = FakeEmbeddingClient(raise_on_batch=LLMUnavailable(_OUTAGE))
    with pytest.raises(LLMUnavailable):
        await _pipeline(down, store, batch_size=10).ingest(str(f))

    third = await _pipeline(FakeEmbeddingClient(), store, batch_size=10).ingest(str(f))
    assert third == IngestReport(documents=1, chunks=2, batches=1, deleted_sources=1)


async def test_shrunk_document_still_drops_orphan_chunks(tmp_path: Path) -> None:
    """The reorder must not cost the property the delete existed for.

    Ids are ``{source}#{index}``, so upserting a shrunk document over its own
    prior vectors would overwrite only the surviving prefix. The delete must
    still happen — just later.
    """
    f, store = await _ingest_once(tmp_path, "a b c d e f g h i")  # 3 chunks
    assert sorted(store.records) == [f"{f.as_posix()}#{i}" for i in range(3)]

    f.write_text("a b c", encoding="utf-8")  # shrunk to 1 chunk
    report = await _pipeline(FakeEmbeddingClient(), store, batch_size=10).ingest(str(f))

    assert sorted(store.records) == [f"{f.as_posix()}#0"]
    assert report.deleted_sources == 1


# ── Per-source ingest ledger (operator observability) ─────────────────────────
#
# Same discipline as the section above: assert on `caplog.records` and the
# structured `extra=` fields, never on `caplog.text`.


def _record(caplog: pytest.LogCaptureFixture, event: str) -> logging.LogRecord:
    """The single record carrying ``event``; fails loudly if absent."""
    return next(r for r in caplog.records if getattr(r, "event", "") == event)


# Everything `logging` puts on a record by itself, so `_extras` can isolate the
# fields this module chose to attach. Derived from a real record rather than
# hand-listed: a hand-list silently goes stale across Python versions (3.12
# added `taskName`), and the stale name would be scanned as if it were ours.
_STANDARD_RECORD_ATTRS = frozenset(
    vars(logging.LogRecord("n", logging.INFO, "p", 0, "m", None, None))
) | {"asctime", "message"}


def _extras(record: logging.LogRecord) -> dict[str, object]:
    """Only the fields the pipeline passed via ``extra=``."""
    return {k: v for k, v in vars(record).items() if k not in _STANDARD_RECORD_ATTRS}


def _looks_like_a_vector(value: object) -> bool:
    """True for a list of numbers (or a list of those) — i.e. an embedding."""
    if not isinstance(value, list):
        return False
    return any(isinstance(item, int | float) or _looks_like_a_vector(item) for item in value)


async def test_replace_logs_what_was_deleted_and_what_was_upserted(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """One INFO line per source answers "what did that run do to my index?"."""
    f, store = await _ingest_once(tmp_path, "a b c d e f g h i")  # 3 chunks

    # The fixture ingest above logs its own ledger line. `at_level` raises the
    # logger's level but does not empty the handler, and a prior test that left
    # the root logger at INFO makes that setup line visible here — so drop
    # everything predating the run actually under test.
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="mangomas.rag.pipeline"):
        await _pipeline(FakeEmbeddingClient(), store, batch_size=2).ingest(str(f))

    replaced = _record(caplog, "rag_source_replaced")
    # getattr: LogRecord has no static schema for `extra=` fields.
    assert getattr(replaced, "source", None) == f.as_posix()
    assert getattr(replaced, "removed", None) == 3  # the prior generation
    assert getattr(replaced, "upserted", None) == 3  # the new one
    assert getattr(replaced, "batches", None) == 2


async def test_first_ingest_logs_zero_removed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """``removed`` in the ledger tracks the store, not the document count."""
    f = tmp_path / "doc.txt"
    f.write_text("one two three", encoding="utf-8")

    with caplog.at_level(logging.INFO, logger="mangomas.rag.pipeline"):
        await _pipeline(FakeEmbeddingClient(), FakeVectorStore(), batch_size=10).ingest(str(f))

    replaced = _record(caplog, "rag_source_replaced")
    assert getattr(replaced, "removed", None) == 0
    assert getattr(replaced, "upserted", None) == 1


async def test_embedding_failure_is_logged_as_leaving_the_index_unchanged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The outage must be legible: which source, how far it got, and that the
    index survived. A bare traceback says none of that."""
    f, store = await _ingest_once(tmp_path, "a b c d e f g h i")
    down = FakeEmbeddingClient(raise_on_batch=LLMUnavailable(_OUTAGE), raise_after_batches=1)

    caplog.clear()  # drop the fixture ingest's own ledger line (see above)
    with (
        caplog.at_level(logging.INFO, logger="mangomas.rag.pipeline"),
        pytest.raises(LLMUnavailable),
    ):
        await _pipeline(down, store, batch_size=2).ingest(str(f))

    failed = _record(caplog, "rag_source_embed_failed")
    assert failed.levelno == logging.ERROR
    assert getattr(failed, "source", None) == f.as_posix()
    assert getattr(failed, "chunks", None) == 3
    assert getattr(failed, "batches_completed", None) == 1
    assert "index left unchanged" in failed.getMessage()
    # No swap was attempted, so there is no ledger line to mislead the operator.
    assert "rag_source_replaced" not in _events(caplog)


async def test_ingest_never_logs_document_text_or_embeddings(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Ingest logs carry counts and ids, never content.

    The same rule ``Retriever.search`` follows for the query: the RAG layer
    cannot know whether a corpus holds anything sensitive, so no document text
    and no embedding vector may reach a log record — not in the message, and
    not in a structured field.
    """
    confidential = "alpha bravo charlie delta echo foxtrot"
    f = tmp_path / "doc.txt"
    f.write_text(confidential, encoding="utf-8")

    with caplog.at_level(logging.DEBUG, logger="mangomas.rag.pipeline"):
        await _pipeline(FakeEmbeddingClient(), FakeVectorStore(), batch_size=1).ingest(str(f))

    assert caplog.records, "fixture must produce records to inspect"
    assert any(_extras(r) for r in caplog.records), "fixture must produce extras to inspect"
    words = confidential.split()
    for record in caplog.records:
        assert not any(w in record.getMessage() for w in words)
        for name, value in _extras(record).items():
            assert not _looks_like_a_vector(value), f"embedding leaked via {name}"
            assert not any(w in str(value) for w in words), f"document text leaked via {name}"
