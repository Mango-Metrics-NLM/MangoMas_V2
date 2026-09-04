"""Tier-3 E3: ``ToolAgent`` retrieves through a real embedding backend.

The real-compute twin of ``tests/rag/test_tool_agent_retrieval.py``, which
drives the same path with ``FakeEmbeddingClient`` + ``FakeVectorStore``. The
fake version proves the wiring; this proves the wiring survives a real
embedding space, where "the query matches the right chunk" is a property of
the model rather than of a stub that returns whatever it was given.

The LLM stays fake on purpose. Two live dependencies in one test would make a
failure unattributable — and the tool-call protocol is exactly the part that
must not depend on which model is loaded (spec-0029 R2.3).

Skipped unless ``RUN_EMBEDDINGS_LOCAL=1`` **and** ``RUN_RAG=1``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from mangomas.agents import ToolAgent
from mangomas.config import RagSettings
from mangomas.core.agent import AgentContext, AgentRequest, Message
from mangomas.core.tools import ToolRegistry
from mangomas.rag import IngestionPipeline, Retriever
from mangomas.rag.retrieval import RetrievalTool
from mangomas.registry import Registry
from tests.constants import (
    DEFAULT_LOCAL_EMBEDDING_MODEL,
    DEFAULT_VECTOR_TOP_K,
    RAG_DEVICE_CORPUS,
    RAG_DEVICE_EXPECTED_TOP_SOURCE,
    RAG_DEVICE_QUERY,
)
from tests.fakes import FakeLLM

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.embeddings_local, pytest.mark.rag]

_FINAL_ANSWER = "Final answer grounded in the retrieved context."
_TOOL_MESSAGE_ROLE = "tool"


def _make_embeddings() -> Any:
    from mangomas.adapters.embeddings.sentence_transformers import (  # noqa: PLC0415
        SentenceTransformersEmbeddingClient,
    )

    return SentenceTransformersEmbeddingClient(model=DEFAULT_LOCAL_EMBEDDING_MODEL)


def _make_store(persist_dir: Path) -> Any:
    from mangomas.adapters.vector.chroma import ChromaVectorStore  # noqa: PLC0415

    return ChromaVectorStore(persist_dir=str(persist_dir), collection_name="e2e-tool-agent")


async def test_tool_agent_grounds_its_answer_in_real_retrieval(tmp_path: Path) -> None:
    """The retrieved chunk reaches the LLM as a ``tool`` message.

    Oracle: the *source document* the retriever chose appears in the
    re-injected tool message. Asserting on the retrieved text rather than on
    a similarity score keeps this device-independent (spec-0029 R2.2) — the
    score differs across devices, the choice does not.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for name, text in RAG_DEVICE_CORPUS.items():
        (corpus / name).write_text(text, encoding="utf-8")

    embeddings = _make_embeddings()
    store = _make_store(tmp_path / "chroma")
    try:
        pipeline = IngestionPipeline(
            embeddings=embeddings,
            vector_store=store,
            settings=RagSettings(chunk_words=50, chunk_overlap=10, min_chunk_words=1),
            batch_size=8,
        )
        report = await pipeline.ingest(str(corpus))
        assert report.documents == len(RAG_DEVICE_CORPUS)

        retriever = Retriever(embeddings=embeddings, vector_store=store, top_k=DEFAULT_VECTOR_TOP_K)
        tool = RetrievalTool(retriever)
        registry: ToolRegistry = Registry("tool")
        registry.register(tool.name, tool)

        # First turn emits a retrieve call; second returns prose. Scripted so
        # the tool protocol is exercised regardless of which model is loaded.
        llm = FakeLLM(
            replies=[
                json.dumps({"tool": tool.name, "arguments": {"query": RAG_DEVICE_QUERY}}),
                _FINAL_ANSWER,
            ]
        )
        ctx = AgentContext(
            llm=llm, repo=None, embeddings=embeddings, vector_store=store, tools=registry
        )
        request = AgentRequest(messages=[Message(role="user", content=RAG_DEVICE_QUERY)])

        response = await ToolAgent().handle(request, ctx)

        assert response.content == _FINAL_ANSWER
        # The second LLM call carries the tool result, which must contain the
        # text of the document real retrieval selected.
        second_call = llm.calls[1]
        tool_messages = [m for m in second_call if m.role == _TOOL_MESSAGE_ROLE]
        assert tool_messages, f"expected a tool message in the second call: {second_call}"
        injected = " ".join(m.content for m in tool_messages)
        expected_text = RAG_DEVICE_CORPUS[RAG_DEVICE_EXPECTED_TOP_SOURCE]
        assert expected_text in injected, (
            f"real retrieval should have grounded the answer in "
            f"{RAG_DEVICE_EXPECTED_TOP_SOURCE}; got {injected!r}"
        )
        logger.info("ToolAgent grounded its answer via real retrieval")
    finally:
        await embeddings.aclose()
        await store.aclose()
