"""RAG tool building factory.

Factories for instantiating RAG tools and retrieval pipelines from
embedding and vector store backends.
"""

from __future__ import annotations

import logging
from typing import Any

from mangomas.core.tools import ToolRegistry
from mangomas.registry import Registry

logger = logging.getLogger(__name__)


def _build_rag_tools(
    embeddings: Any | None,
    vector_store: Any | None,
    top_k: int,
) -> ToolRegistry | None:
    """Return a ToolRegistry holding a RetrievalTool, or ``None`` if RAG is off.

    Requires *both* an embedding client and a vector store — retrieval needs to
    embed the query and search the index. When either is absent, no tools are
    wired and ``ctx.tools`` stays ``None`` (unchanged default behaviour).
    """
    if embeddings is None or vector_store is None:
        logger.debug("RAG disabled: embeddings and vector_store both required")
        return None

    logger.debug(
        "Building RAG tools",
        extra={"top_k": top_k, "embeddings": type(embeddings).__name__},
    )
    from mangomas.rag import RetrievalTool, Retriever  # noqa: PLC0415

    retriever = Retriever(embeddings=embeddings, vector_store=vector_store, top_k=top_k)
    tool = RetrievalTool(retriever)
    registry: ToolRegistry = Registry("tool")
    registry.register(tool.name, tool)
    logger.info("RAG retrieval tool registered (top_k=%d)", top_k)
    return registry


__all__ = [
    "_build_rag_tools",
]
