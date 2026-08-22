"""Tests for the RAG Retriever and RetrievalTool (fakes only)."""

from __future__ import annotations

import json
import logging

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

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


# ── Instrumentation (spec-0023 R2, query side) ────────────────────────────────
#
# The query half was silent while the ingest half was not, which left the two
# most-reported RAG symptoms indistinguishable in the logs: "the store is
# empty", "my document was dropped at ingest", and "the query genuinely matches
# nothing" all produced zero results and no explanation.
#
# Assertions read the structured ``extra=`` fields off ``caplog.records``, not
# ``caplog.text``: the default formatter renders only the message, so a test
# reading the text passes even if every ``extra`` field is dropped.


def _events(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [getattr(record, "event", "") for record in caplog.records]


async def test_search_warns_when_nothing_matches(caplog: pytest.LogCaptureFixture) -> None:
    """A zero-result search is the case a user actually reports."""
    retriever = Retriever(embeddings=FakeEmbeddingClient(), vector_store=FakeVectorStore(), top_k=3)

    with caplog.at_level(logging.WARNING, logger="mangomas.rag.retrieval"):
        results = await retriever.search("anything")

    assert results == []
    assert "rag_search_empty" in _events(caplog)
    record = next(r for r in caplog.records if getattr(r, "event", "") == "rag_search_empty")
    assert getattr(record, "top_k", None) == 3


async def test_search_logs_shape_but_never_the_query(caplog: pytest.LogCaptureFixture) -> None:
    """A successful search reports its shape — and must not log the query text.

    The query is end-user content and the RAG layer cannot know whether it
    carries anything sensitive, so only its length is recorded. This asserts
    the absence directly: a later 'just add the query, it helps debugging'
    edit fails here rather than shipping user text into the log stream.
    """
    emb = FakeEmbeddingClient()
    store = FakeVectorStore()
    await _seed(store, emb)
    retriever = Retriever(embeddings=emb, vector_store=store, top_k=5)
    private_query = "my-private-search-string"

    with caplog.at_level(logging.DEBUG, logger="mangomas.rag.retrieval"):
        results = await retriever.search(private_query)

    assert results
    record = next(r for r in caplog.records if getattr(r, "event", "") == "rag_search_completed")
    assert getattr(record, "matches", None) == len(results)
    assert getattr(record, "sources", None) == sorted({r.chunk.source for r in results})
    assert getattr(record, "query_chars", None) == len(private_query)
    assert private_query not in str(record.__dict__), "the query text must never be logged"
    assert "rag_search_empty" not in _events(caplog)


async def test_tool_warns_when_the_model_omits_the_query(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A `retrieve` call with no query is a prompt/schema problem, not a miss.

    Returning the string alone tells the model and nobody else; the operator
    needs to see that the tool is being called wrong.
    """
    retriever = Retriever(embeddings=FakeEmbeddingClient(), vector_store=FakeVectorStore(), top_k=2)

    with caplog.at_level(logging.WARNING, logger="mangomas.rag.retrieval"):
        output = await RetrievalTool(retriever).execute({"top_k": 2})

    assert output == "No query provided."
    assert "rag_tool_missing_query" in _events(caplog)
    record = next(r for r in caplog.records if getattr(r, "event", "") == "rag_tool_missing_query")
    assert getattr(record, "argument_keys", None) == ["top_k"]


async def test_search_emits_a_span() -> None:
    """`rag.search` mirrors `rag.ingest`, so a trace shows both halves.

    Acquired via `trace.get_tracer(__name__)` inside the call, never bound at
    module scope — a module-level `mangomas.telemetry.get_tracer` would
    configure telemetry at import with hard-coded defaults (spec-0023 R1a).

    Attaches an exporter to whichever provider is live rather than swapping the
    global one. OTel promotes `ProxyTracerProvider` → `TracerProvider` exactly
    once and refuses to go back: assigning the proxy to `trace._TRACER_PROVIDER`
    to "restore" it makes `get_tracer` delegate to itself, and the next test in
    the same process dies with a `RecursionError` rather than a useful failure.
    This is the pattern `tests/test_tracing.py` already uses for that reason.
    """
    exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    emb = FakeEmbeddingClient()
    store = FakeVectorStore()
    await _seed(store, emb)
    await Retriever(embeddings=emb, vector_store=store, top_k=2).search("q")

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert "rag.search" in spans, f"expected a rag.search span, got {sorted(spans)}"
    attributes = spans["rag.search"].attributes or {}
    assert attributes["rag.top_k"] == 2
    matches = attributes["rag.matches"]
    assert isinstance(matches, int)
    assert matches >= 1
