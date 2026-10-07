"""Regression tests for defects found while building spec-0035 (Docling ingestion).

Each test pins one defect that a reviewer or a property test caught on the
``feat/docling-ingestion`` branch, independently of the unit files, so a later
refactor of those files cannot silently drop the guard.

1. **Word chunker flattened parsed Markdown** — ``chunk_text`` re-joins words
   with single spaces, destroying table rows; parsed documents now use
   ``chunk_lines`` (review blocker, rev-2 plan).
2. **Leading whitespace dropped** — a ``\\S+\\s*`` tokenizer lost indentation
   before the first token (PR #82 review).
3. **Parse failure purged a good index entry** — a failed parse must never
   reach the vector-replace step (plan rev-2 / ADR-0036 §5).
4. **Iterator order diverged from load_documents** — sorting POSIX strings put
   ``_.txt`` before ``_/_.txt``; ``Path`` order puts it after (Hypothesis).
5. **Non-finite limits passed validation** — NaN/inf slipped past ``<= 0``
   (PR #83 review).
6. **Framing keyed on an optional argument** — parsed passages went unframed
   when ``parser_name`` was omitted (PR #83 architecture review).
7. **Same-named single files collided** — bare-name sources let
   ``b/report.pdf`` purge ``a/report.pdf`` (PR #83 test review).
"""

from __future__ import annotations

import asyncio
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from mangomas.config import ParserSettings, RagSettings
from mangomas.errors import DocumentParseError
from mangomas.rag.chunker import chunk_lines
from mangomas.rag.loader import (
    META_PARSE_STATUS,
    PARSE_STATUS_SUCCESS,
    iter_documents,
    load_documents,
)
from mangomas.rag.pipeline import IngestionPipeline
from mangomas.rag.retrieval import UNTRUSTED_DOCUMENT_TAG, RetrievalTool, Retriever
from tests.constants.docling import TEST_DOCLING_PDF_BYTES, TEST_LOADER_TABLE_MARKDOWN
from tests.fakes import FakeDocumentParser, FakeEmbeddingClient, FakeVectorStore


def test_defect_1_parsed_tables_keep_their_line_structure() -> None:
    assert chunk_lines(TEST_LOADER_TABLE_MARKDOWN, size=50, overlap=0) == [
        TEST_LOADER_TABLE_MARKDOWN
    ]


def test_defect_2_leading_whitespace_survives() -> None:
    text = "    indented\n"
    assert chunk_lines(text, size=5, overlap=0) == [text]


async def test_defect_3_a_parse_failure_never_purges(tmp_path: Path) -> None:
    (tmp_path / "a.pdf").write_bytes(TEST_DOCLING_PDF_BYTES)
    store, emb = FakeVectorStore(), FakeEmbeddingClient()
    await store.upsert(
        ids=["a.pdf#0"],
        embeddings=[await emb.embed("old")],
        documents=["old"],
        metadatas=[{"source": "a.pdf", "index": 0}],
    )
    pipeline = IngestionPipeline(
        embeddings=emb,
        vector_store=store,
        settings=RagSettings(),
        batch_size=1,
        parser=FakeDocumentParser(default=DocumentParseError("down")),
        parser_settings=ParserSettings(enabled=True),
    )
    await pipeline.ingest(str(tmp_path))
    assert store.deleted_sources == []
    assert [r["document"] for r in store.records.values()] == ["old"]


def test_defect_4_iterator_order_matches_load_documents(tmp_path: Path) -> None:
    (tmp_path / "_.txt").write_text("a", encoding="utf-8")
    (tmp_path / "_").mkdir()
    (tmp_path / "_" / "_.txt").write_text("b", encoding="utf-8")

    async def _both() -> tuple[list[str], list[str]]:
        streamed = [d.source async for d in iter_documents(str(tmp_path))]
        listed = [d.source for d in await load_documents(str(tmp_path))]
        return streamed, listed

    streamed, listed = asyncio.run(_both())
    assert streamed == listed == ["_/_.txt", "_.txt"]


@pytest.mark.parametrize("bad", [math.nan, math.inf])
def test_defect_5_non_finite_limits_are_rejected(bad: float) -> None:
    with pytest.raises(ValidationError):
        ParserSettings(max_zip_ratio=bad)


async def test_defect_6_framing_does_not_need_a_parser_name() -> None:
    store, emb = FakeVectorStore(), FakeEmbeddingClient()
    await store.upsert(
        ids=["d#0"],
        embeddings=[await emb.embed("q")],
        documents=["body"],
        metadatas=[{"source": "d.pdf", "index": 0, META_PARSE_STATUS: PARSE_STATUS_SUCCESS}],
    )
    out = await RetrievalTool(Retriever(embeddings=emb, vector_store=store, top_k=1)).execute(
        {"query": "q"}
    )
    assert f"<{UNTRUSTED_DOCUMENT_TAG} " in out


async def test_defect_7_same_named_single_files_do_not_collide(tmp_path: Path) -> None:
    settings = ParserSettings(enabled=True)
    sources = []
    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
        f = tmp_path / folder / "report.pdf"
        f.write_bytes(TEST_DOCLING_PDF_BYTES)
        parser = FakeDocumentParser(default=TEST_LOADER_TABLE_MARKDOWN)
        sources += [
            d.source async for d in iter_documents(str(f), parser=parser, settings=settings)
        ]
    assert len(set(sources)) == 2
