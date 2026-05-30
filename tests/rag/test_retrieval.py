"""Tests for the RAG Retriever and RetrievalTool (fakes only)."""

from __future__ import annotations

import json

from mangomas.core.tools import Tool, ToolCallParser
from mangomas.rag.retrieval import RetrievalTool, Retriever
from tests.fakes import FakeEmbeddingClient, FakeVectorStore


async def _seed(store: FakeVectorStore, emb: FakeEmbeddingClient) -> None:
    """Upsert two docs whose embeddings come from the fake client."""
    docs = {"doc one": "a.txt#0", "doc two": "b.txt#0"}
    for text, doc_id in docs.items():
        vec = await emb.embed(text)
        await store.upsert(
            ids=[doc_id],
            embeddings=[vec],
            documents=[text],
            metadatas=[{"source": doc_id.split("#")[0], "index": 0}],
        )


async def test_retriever_search_maps_matches_to_results() -> None:
    emb = FakeEmbeddingClient()
    store = FakeVectorStore()
    await _seed(store, emb)
    retriever = Retriever(embeddings=emb, vector_store=store, top_k=5)

    results = await retriever.search("doc one")
    assert results
    top = results[0]
    assert top.chunk.text == "doc one"
    assert top.chunk.source == "a.txt"
    assert top.chunk.index == 0
    assert 0.0 <= top.score <= 1.0


async def test_retriever_respects_top_k_override() -> None:
    emb = FakeEmbeddingClient()
    store = FakeVectorStore()
    await _seed(store, emb)
    retriever = Retriever(embeddings=emb, vector_store=store, top_k=5)

    results = await retriever.search("doc one", top_k=1)
    assert len(results) == 1


async def test_retriever_floors_top_k_at_one() -> None:
    emb = FakeEmbeddingClient()
    store = FakeVectorStore()
    await _seed(store, emb)
    retriever = Retriever(embeddings=emb, vector_store=store, top_k=0)
    results = await retriever.search("doc one")
    assert len(results) == 1


async def test_retrieval_tool_satisfies_protocol() -> None:
    tool = RetrievalTool(
        Retriever(embeddings=FakeEmbeddingClient(), vector_store=FakeVectorStore(), top_k=3)
    )
    assert isinstance(tool, Tool)
    assert tool.name == "retrieve"
    assert tool.spec.name == "retrieve"
    assert "query" in tool.spec.parameters_schema["properties"]


async def test_retrieval_tool_execute_formats_context() -> None:
    emb = FakeEmbeddingClient()
    store = FakeVectorStore()
    await _seed(store, emb)
    tool = RetrievalTool(Retriever(embeddings=emb, vector_store=store, top_k=5))

    out = await tool.execute({"query": "doc one"})
    assert "doc one" in out
    assert "score=" in out
    assert "source=a.txt" in out


async def test_retrieval_tool_execute_empty_query() -> None:
    tool = RetrievalTool(
        Retriever(embeddings=FakeEmbeddingClient(), vector_store=FakeVectorStore(), top_k=3)
    )
    assert await tool.execute({"query": "   "}) == "No query provided."
    assert await tool.execute({}) == "No query provided."


async def test_retrieval_tool_execute_no_results() -> None:
    tool = RetrievalTool(
        Retriever(embeddings=FakeEmbeddingClient(), vector_store=FakeVectorStore(), top_k=3)
    )
    assert await tool.execute({"query": "anything"}) == "No relevant context found."


async def test_retrieval_tool_execute_top_k_override() -> None:
    emb = FakeEmbeddingClient()
    store = FakeVectorStore()
    await _seed(store, emb)
    tool = RetrievalTool(Retriever(embeddings=emb, vector_store=store, top_k=5))
    out = await tool.execute({"query": "doc one", "top_k": 1})
    # Only one ranked passage when top_k=1.
    assert out.count("score=") == 1


async def test_retrieval_tool_call_round_trips_through_parser() -> None:
    """A LLM-style tool-call JSON parses to the retrieve tool name."""
    emb = FakeEmbeddingClient()
    store = FakeVectorStore()
    await _seed(store, emb)
    tool = RetrievalTool(Retriever(embeddings=emb, vector_store=store, top_k=5))

    raw = json.dumps({"tool": "retrieve", "arguments": {"query": "doc two"}})
    call = ToolCallParser().parse(raw)
    assert call is not None
    assert call.tool == tool.name
    out = await tool.execute(call.arguments)
    assert "doc two" in out
