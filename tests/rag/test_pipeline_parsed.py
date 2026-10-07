"""Parser-enabled ingestion (spec-0035 R7/R8 / M6).

The central guarantee: a document that fails to parse never reaches the code
that deletes a source's stored vectors, so a corrupt or unreachable file can
never empty a good index entry. Each test pins one behaviour.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mangomas.adapters.parsers.base import ParsedDocument
from mangomas.config import ParserSettings, RagSettings
from mangomas.errors import ConfigError, DocumentParseError
from mangomas.rag import pipeline as pipeline_module
from mangomas.rag.loader import RawDoc
from mangomas.rag.pipeline import (
    CHUNKER_LINES,
    META_CHUNK_WORDS,
    META_CHUNKER,
    META_EMBEDDING_MODEL,
    IngestionPipeline,
    IngestReport,
    _flatten_metadata,
)
from tests.constants.docling import (
    SPEC_DOCLING_PROVIDER,
    SPEC_LOADER_META_PARSE_STATUS,
    SPEC_LOADER_META_PARSER,
    SPEC_LOADER_STATUS_PARTIAL,
    SPEC_PIPELINE_EVENT_EMPTY,
    SPEC_PIPELINE_EVENT_OVER_BUDGET,
    SPEC_PIPELINE_EVENT_SKIPPED_PARSE,
    SPEC_PIPELINE_EVENT_STARTED,
    TEST_DOCLING_MAX_FILE_BYTES,
    TEST_DOCLING_PDF_BYTES,
    TEST_EMBEDDING_MODEL,
    TEST_LOADER_TABLE_MARKDOWN,
    TEST_LOADER_TEXT_BODY,
)
from tests.fakes import FakeDocumentParser, FakeEmbeddingClient, FakeVectorStore

_RAG = RagSettings(chunk_words=3, chunk_overlap=0)
_PARSED_WORDS = 50
_PRESEEDED = ["old passage one", "old passage two"]


def _parser_settings(**overrides: object) -> ParserSettings:
    values: dict[str, object] = {
        "enabled": True,
        "max_file_bytes": TEST_DOCLING_MAX_FILE_BYTES,
        "parsed_chunk_words": _PARSED_WORDS,
    }
    values.update(overrides)
    return ParserSettings.model_validate(values)


def _pipeline(
    store: FakeVectorStore,
    parser: FakeDocumentParser,
    *,
    embedding_model: str | None = TEST_EMBEDDING_MODEL,
    **settings_overrides: object,
) -> IngestionPipeline:
    return IngestionPipeline(
        embeddings=FakeEmbeddingClient(),
        vector_store=store,
        settings=_RAG,
        batch_size=10,
        parser=parser,
        parser_settings=_parser_settings(**settings_overrides),
        parser_name=SPEC_DOCLING_PROVIDER,
        embedding_model=embedding_model,
    )


async def _preseed(store: FakeVectorStore, source: str) -> None:
    await store.upsert(
        ids=[f"{source}#{i}" for i in range(len(_PRESEEDED))],
        embeddings=[[1.0, 0.0] for _ in _PRESEEDED],
        documents=list(_PRESEEDED),
        metadatas=[{"source": source, "index": i} for i in range(len(_PRESEEDED))],
    )


def _records_for(store: FakeVectorStore, source: str) -> list[str]:
    return sorted(
        rec["document"] for rec in store.records.values() if rec["metadata"]["source"] == source
    )


# ── Never purge on a parse failure ────────────────────────────────────────────


async def test_a_skipped_parse_failure_keeps_existing_vectors(tmp_path: Path) -> None:
    (tmp_path / "a.pdf").write_bytes(TEST_DOCLING_PDF_BYTES)
    store = FakeVectorStore()
    await _preseed(store, "a.pdf")
    parser = FakeDocumentParser(default=DocumentParseError("parser down"))

    report = await _pipeline(store, parser).ingest(str(tmp_path))

    assert report == IngestReport(
        documents=0, chunks=0, batches=0, deleted_sources=0, skipped_documents=1
    )
    assert _records_for(store, "a.pdf") == sorted(_PRESEEDED)
    assert store.deleted_sources == []


async def test_empty_text_from_a_non_empty_file_keeps_existing_vectors(tmp_path: Path) -> None:
    (tmp_path / "a.pdf").write_bytes(TEST_DOCLING_PDF_BYTES)
    store = FakeVectorStore()
    await _preseed(store, "a.pdf")
    report = await _pipeline(store, FakeDocumentParser(default="  ")).ingest(str(tmp_path))
    assert report.skipped_documents == 1
    assert _records_for(store, "a.pdf") == sorted(_PRESEEDED)


async def test_an_empty_markdown_file_still_purges_like_before(tmp_path: Path) -> None:
    """Opposite direction: a genuinely empty text document is still a purge."""
    (tmp_path / "a.md").write_text("", encoding="utf-8")
    store = FakeVectorStore()
    await _preseed(store, "a.md")
    report = await _pipeline(store, FakeDocumentParser()).ingest(str(tmp_path))
    assert report.deleted_sources == 1
    assert _records_for(store, "a.md") == []


async def test_on_error_fail_keeps_the_failing_source_and_stops(tmp_path: Path) -> None:
    """Earlier documents are replaced, the failing one keeps its vectors, later ones
    are never touched."""
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        (tmp_path / name).write_bytes(TEST_DOCLING_PDF_BYTES)
    store = FakeVectorStore()
    await _preseed(store, "b.pdf")
    await _preseed(store, "c.pdf")
    parser = FakeDocumentParser(
        outcomes={"b.pdf": DocumentParseError("bad file")}, default=TEST_LOADER_TABLE_MARKDOWN
    )
    with pytest.raises(DocumentParseError):
        await _pipeline(store, parser, on_error="fail").ingest(str(tmp_path))
    assert _records_for(store, "a.pdf") == [TEST_LOADER_TABLE_MARKDOWN]
    assert _records_for(store, "b.pdf") == sorted(_PRESEEDED)
    assert _records_for(store, "c.pdf") == sorted(_PRESEEDED)
    assert [name for name, _ in parser.calls] == ["a.pdf", "b.pdf"]


async def test_a_zero_byte_pdf_that_parses_to_nothing_purges(tmp_path: Path) -> None:
    """Pinned deliberately: an empty file is a genuinely empty document, like an
    empty .md, so its old vectors go."""
    (tmp_path / "a.pdf").write_bytes(b"")
    store = FakeVectorStore()
    await _preseed(store, "a.pdf")
    report = await _pipeline(store, FakeDocumentParser(default="")).ingest(str(tmp_path))
    assert report.deleted_sources == 1
    assert _records_for(store, "a.pdf") == []


async def test_the_parser_path_logs_start_and_finish_inside_the_run(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "a.pdf").write_bytes(TEST_DOCLING_PDF_BYTES)
    caplog.set_level(logging.INFO, logger=pipeline_module.__name__)
    await _pipeline(FakeVectorStore(), FakeDocumentParser(default="x")).ingest(str(tmp_path))
    events = [getattr(r, "event", None) for r in caplog.records]
    assert events[0] == SPEC_PIPELINE_EVENT_STARTED
    assert "rag_ingest_finished" in events


async def test_a_config_error_fails_the_run(tmp_path: Path) -> None:
    (tmp_path / "a.pdf").write_bytes(TEST_DOCLING_PDF_BYTES)
    parser = FakeDocumentParser(default=ConfigError("rejected credentials"))
    with pytest.raises(ConfigError):
        await _pipeline(FakeVectorStore(), parser).ingest(str(tmp_path))


async def test_all_failures_report_skipped_not_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "a.pdf").write_bytes(TEST_DOCLING_PDF_BYTES)
    caplog.set_level(logging.WARNING, logger=pipeline_module.__name__)
    parser = FakeDocumentParser(default=DocumentParseError("nope"))
    report = await _pipeline(FakeVectorStore(), parser).ingest(str(tmp_path))
    assert report.skipped_documents == 1
    events = [getattr(r, "event", None) for r in caplog.records]
    assert SPEC_PIPELINE_EVENT_EMPTY not in events
    assert SPEC_PIPELINE_EVENT_SKIPPED_PARSE in events


async def test_a_truly_empty_directory_still_warns_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=pipeline_module.__name__)
    await _pipeline(FakeVectorStore(), FakeDocumentParser()).ingest(str(tmp_path))
    assert SPEC_PIPELINE_EVENT_EMPTY in [getattr(r, "event", None) for r in caplog.records]


# ── Chunking and metadata ─────────────────────────────────────────────────────


async def test_a_parsed_table_keeps_its_newlines_and_separator(tmp_path: Path) -> None:
    """Regression (review blocker): the word chunker flattened every newline."""
    (tmp_path / "t.pdf").write_bytes(TEST_DOCLING_PDF_BYTES)
    store = FakeVectorStore()
    await _pipeline(store, FakeDocumentParser(default=TEST_LOADER_TABLE_MARKDOWN)).ingest(
        str(tmp_path)
    )
    (stored,) = _records_for(store, "t.pdf")
    assert stored == TEST_LOADER_TABLE_MARKDOWN
    assert "|---|---|\n" in stored


async def test_parsed_chunks_carry_audit_metadata(tmp_path: Path) -> None:
    (tmp_path / "t.pdf").write_bytes(TEST_DOCLING_PDF_BYTES)
    store = FakeVectorStore()
    parser = FakeDocumentParser(
        default=ParsedDocument(text=TEST_LOADER_TABLE_MARKDOWN, partial=True)
    )
    await _pipeline(store, parser).ingest(str(tmp_path))
    (record,) = store.records.values()
    assert record["metadata"] == {
        SPEC_LOADER_META_PARSER: SPEC_DOCLING_PROVIDER,
        SPEC_LOADER_META_PARSE_STATUS: SPEC_LOADER_STATUS_PARTIAL,
        META_CHUNKER: CHUNKER_LINES,
        META_CHUNK_WORDS: _PARSED_WORDS,
        META_EMBEDDING_MODEL: TEST_EMBEDDING_MODEL,
        "source": "t.pdf",
        "index": 0,
    }


async def test_embedding_model_key_is_omitted_when_unknown(tmp_path: Path) -> None:
    (tmp_path / "t.pdf").write_bytes(TEST_DOCLING_PDF_BYTES)
    store = FakeVectorStore()
    parser = FakeDocumentParser(default=TEST_LOADER_TABLE_MARKDOWN)
    await _pipeline(store, parser, embedding_model=None).ingest(str(tmp_path))
    (record,) = store.records.values()
    assert META_EMBEDDING_MODEL not in record["metadata"]


async def test_text_chunks_keep_exactly_source_and_index(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text(TEST_LOADER_TEXT_BODY, encoding="utf-8")
    store = FakeVectorStore()
    await _pipeline(store, FakeDocumentParser()).ingest(str(tmp_path))
    assert {tuple(sorted(r["metadata"])) for r in store.records.values()} == {("index", "source")}


async def test_reingesting_a_text_source_as_parsed_replaces_it(tmp_path: Path) -> None:
    (tmp_path / "doc.pdf").write_bytes(TEST_DOCLING_PDF_BYTES)
    store = FakeVectorStore()
    await _preseed(store, "doc.pdf")
    await _pipeline(store, FakeDocumentParser(default=TEST_LOADER_TABLE_MARKDOWN)).ingest(
        str(tmp_path)
    )
    assert _records_for(store, "doc.pdf") == [TEST_LOADER_TABLE_MARKDOWN]


def test_reserved_keys_always_win() -> None:
    flat = _flatten_metadata({"source": "forged", "index": 99, "kept": "yes"})
    assert flat == {"kept": "yes"}


@settings(max_examples=60, deadline=None)
@given(
    st.dictionaries(
        st.one_of(st.text(max_size=5), st.integers()),
        st.recursive(
            st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text(),
            lambda children: (
                st.lists(children, max_size=3)
                | st.dictionaries(st.text(max_size=3), children, max_size=3)
            ),
            max_leaves=5,
        ),
        max_size=6,
    )
)
def test_flattened_metadata_is_always_store_legal(raw: dict[object, object]) -> None:
    flat = _flatten_metadata(raw)  # type: ignore[arg-type]
    for key, value in flat.items():
        assert isinstance(key, str)
        assert key not in {"source", "index"}
        assert isinstance(value, (str, int, float, bool))


# ── Budget warning ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("budget", "warns"), [(1, True), (10_000, False)])
async def test_over_budget_chunks_are_reported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, budget: int, warns: bool
) -> None:
    (tmp_path / "t.pdf").write_bytes(TEST_DOCLING_PDF_BYTES)
    caplog.set_level(logging.WARNING, logger=pipeline_module.__name__)
    parser = FakeDocumentParser(default=TEST_LOADER_TABLE_MARKDOWN)
    await _pipeline(FakeVectorStore(), parser, embed_max_tokens=budget).ingest(str(tmp_path))
    events = [getattr(r, "event", None) for r in caplog.records]
    assert (SPEC_PIPELINE_EVENT_OVER_BUDGET in events) is warns


# ── Construction ──────────────────────────────────────────────────────────────


def test_a_parser_without_settings_is_rejected() -> None:
    with pytest.raises(ValueError, match="parser_settings"):
        IngestionPipeline(
            embeddings=FakeEmbeddingClient(),
            vector_store=FakeVectorStore(),
            settings=_RAG,
            batch_size=1,
            parser=FakeDocumentParser(),
        )


def test_rawdoc_without_metadata_uses_the_word_chunker() -> None:
    pipe = _pipeline(FakeVectorStore(), FakeDocumentParser())
    texts, extra = pipe._chunk(RawDoc(source="a", text="one two three four"))
    assert texts == ["one two three", "four"]
    assert extra == {}
