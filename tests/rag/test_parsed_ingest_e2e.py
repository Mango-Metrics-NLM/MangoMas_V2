"""End-to-end chain for parser-enabled RAG (spec-0035, respx-mocked serve).

Composition-built ``DoclingServeParser`` → ``iter_documents`` →
``IngestionPipeline`` → vector store → ``RetrievalTool``, with only the HTTP
boundary and the embedding/vector backends faked. It proves the pieces agree
on the contract the unit tests pin separately: a parsed table survives intact,
its chunk carries the audit metadata, a failed parse leaves the index alone,
and retrieval frames the parsed passage as untrusted.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from mangomas.composition.parser import build_parser
from mangomas.config import ParserSettings, RagSettings
from mangomas.rag import IngestionPipeline, IngestReport, RetrievalTool, Retriever
from mangomas.rag.retrieval import UNTRUSTED_DOCUMENT_TAG
from tests.constants.docling import (
    DOCLING_FIXTURE_FAILURE,
    DOCLING_FIXTURE_SUCCESS,
    SPEC_DOCLING_CONVERT_PATH,
    SPEC_DOCLING_PROVIDER,
    TEST_DOCLING_BASE_URL,
    TEST_DOCLING_PDF_BYTES,
    TEST_EMBEDDING_MODEL,
    load_docling_fixture,
)
from tests.fakes import FakeEmbeddingClient, FakeVectorStore

_CONVERT_URL = f"{TEST_DOCLING_BASE_URL}{SPEC_DOCLING_CONVERT_PATH}"


def _parser_settings() -> ParserSettings:
    return ParserSettings(enabled=True, base_url=TEST_DOCLING_BASE_URL)


async def _ingest(tmp_path: Path, store: FakeVectorStore, emb: FakeEmbeddingClient) -> IngestReport:
    settings = _parser_settings()
    parser = build_parser(settings, secrets=None)
    assert parser is not None
    try:
        pipeline = IngestionPipeline(
            embeddings=emb,
            vector_store=store,
            settings=RagSettings(),
            batch_size=8,
            parser=parser,
            parser_settings=settings,
            parser_name=settings.provider,
            embedding_model=TEST_EMBEDDING_MODEL,
        )
        return await pipeline.ingest(str(tmp_path))
    finally:
        await parser.aclose()


@respx.mock
async def test_a_parsed_pdf_is_indexed_intact_and_retrieved_framed(tmp_path: Path) -> None:
    body = load_docling_fixture(DOCLING_FIXTURE_SUCCESS)
    respx.post(_CONVERT_URL).mock(return_value=httpx.Response(200, json=body))
    (tmp_path / "report.pdf").write_bytes(TEST_DOCLING_PDF_BYTES)
    (tmp_path / "notes.md").write_text("plain notes", encoding="utf-8")
    store, emb = FakeVectorStore(), FakeEmbeddingClient()

    report = await _ingest(tmp_path, store, emb)

    assert report.documents == 2
    assert report.skipped_documents == 0
    parsed = [r for r in store.records.values() if r["metadata"]["source"] == "report.pdf"]
    assert [r["document"] for r in parsed] == [body["document"]["md_content"]]
    assert parsed[0]["metadata"]["parser"] == SPEC_DOCLING_PROVIDER
    assert parsed[0]["metadata"]["embedding_model"] == TEST_EMBEDDING_MODEL

    tool = RetrievalTool(Retriever(embeddings=emb, vector_store=store, top_k=2))
    out = await tool.execute({"query": body["document"]["md_content"]})
    assert out.count(f"</{UNTRUSTED_DOCUMENT_TAG}>") == 1
    assert "plain notes" in out


@respx.mock
async def test_a_failed_conversion_leaves_the_index_untouched(tmp_path: Path) -> None:
    respx.post(_CONVERT_URL).mock(
        return_value=httpx.Response(200, json=load_docling_fixture(DOCLING_FIXTURE_FAILURE))
    )
    (tmp_path / "report.pdf").write_bytes(TEST_DOCLING_PDF_BYTES)
    store, emb = FakeVectorStore(), FakeEmbeddingClient()
    await store.upsert(
        ids=["report.pdf#0"],
        embeddings=[await emb.embed("old")],
        documents=["old passage"],
        metadatas=[{"source": "report.pdf", "index": 0}],
    )

    report = await _ingest(tmp_path, store, emb)

    assert report.skipped_documents == 1
    assert [r["document"] for r in store.records.values()] == ["old passage"]
    assert store.deleted_sources == []
