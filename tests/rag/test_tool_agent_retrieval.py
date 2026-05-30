"""Integration: ToolAgent discovers and invokes the RetrievalTool via ctx.tools."""

from __future__ import annotations

import json

from mangomas.agents import ToolAgent
from mangomas.core.agent import AgentContext, AgentRequest, Message
from mangomas.core.tools import ToolRegistry
from mangomas.rag.retrieval import RetrievalTool, Retriever
from mangomas.registry import Registry
from tests.fakes import FakeEmbeddingClient, FakeLLM, FakeVectorStore


async def test_tool_agent_invokes_retrieval_tool() -> None:
    emb = FakeEmbeddingClient()
    store = FakeVectorStore()
    vec = await emb.embed("grounded fact")
    await store.upsert(
        ids=["kb.txt#0"],
        embeddings=[vec],
        documents=["grounded fact"],
        metadatas=[{"source": "kb.txt", "index": 0}],
    )

    tool = RetrievalTool(Retriever(embeddings=emb, vector_store=store, top_k=5))
    registry: ToolRegistry = Registry("tool")
    registry.register(tool.name, tool)

    # First LLM turn emits a retrieve tool call; second turn returns prose.
    llm = FakeLLM(
        replies=[
            json.dumps({"tool": "retrieve", "arguments": {"query": "grounded fact"}}),
            "Final answer using the retrieved context.",
        ]
    )
    ctx = AgentContext(llm=llm, repo=None, embeddings=emb, vector_store=store, tools=registry)
    request = AgentRequest(messages=[Message(role="user", content="what is the fact?")])

    response = await ToolAgent().handle(request, ctx)

    assert response.content == "Final answer using the retrieved context."
    # The tool result (containing the retrieved document) was re-injected as a
    # 'tool' message before the second LLM call.
    second_call = llm.calls[1]
    tool_messages = [m for m in second_call if m.role == "tool"]
    assert tool_messages
    assert "grounded fact" in tool_messages[-1].content
