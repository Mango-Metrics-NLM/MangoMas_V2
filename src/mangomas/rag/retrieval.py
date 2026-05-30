"""Query-time retrieval: embed a query, search the vector store, format context.

:class:`Retriever` is the pure retrieval primitive (embed → query → map). It maps
the vector adapter's primitive :class:`~mangomas.adapters.vector.base.VectorMatch`
back into the RAG layer's own :class:`~mangomas.rag.models.SearchResult` /
:class:`~mangomas.rag.models.Chunk` vocabulary, preserving the
``rag → adapters (protocols only)`` dependency direction.

:class:`RetrievalTool` adapts a :class:`Retriever` to the
:class:`~mangomas.core.tools.Tool` protocol so a :class:`~mangomas.agents.ToolAgent`
can pull retrieved context into a conversation via a structured tool call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mangomas.core.tools import ToolSpec
from mangomas.rag.models import Chunk, SearchResult

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.adapters.embeddings.base import EmbeddingClient
    from mangomas.adapters.vector.base import VectorMatch, VectorStoreRepository

__all__ = ["RetrievalTool", "Retriever"]

_TOOL_NAME = "retrieve"


def _match_to_result(match: VectorMatch) -> SearchResult:
    """Map a primitive :class:`VectorMatch` to a RAG :class:`SearchResult`."""
    metadata = dict(match.metadata)
    source = str(metadata.get("source", ""))
    raw_index = metadata.get("index", 0)
    index = raw_index if isinstance(raw_index, int) else 0
    chunk = Chunk(
        id=match.id,
        text=match.document,
        source=source,
        index=index,
        metadata=metadata,
    )
    return SearchResult(chunk=chunk, score=match.score)


class Retriever:
    """Embed a query and return the nearest stored chunks as search results."""

    def __init__(
        self,
        *,
        embeddings: EmbeddingClient,
        vector_store: VectorStoreRepository,
        top_k: int,
    ) -> None:
        self._embeddings = embeddings
        self._vector_store = vector_store
        self._top_k = max(1, top_k)

    async def search(self, query: str, *, top_k: int | None = None) -> list[SearchResult]:
        """Return up to ``top_k`` (default the configured depth) results for ``query``."""
        k = self._top_k if top_k is None else max(1, top_k)
        embedding = await self._embeddings.embed(query)
        matches = await self._vector_store.query(embedding=embedding, top_k=k)
        return [_match_to_result(m) for m in matches]


class RetrievalTool:
    """A :class:`~mangomas.core.tools.Tool` that injects retrieved context.

    Satisfies the Tool protocol (``name`` / ``spec`` / ``execute``). ``execute``
    accepts ``{"query": str, "top_k"?: int}`` and returns a formatted, ranked
    context block suitable for re-injection into the conversation.
    """

    name = _TOOL_NAME

    def __init__(self, retriever: Retriever) -> None:
        self._retriever = retriever

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=(
                "Retrieve relevant context passages from the knowledge base. "
                "Use when you need grounded facts to answer the user."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query."},
                    "top_k": {
                        "type": "integer",
                        "description": "Maximum passages to return (optional).",
                    },
                },
                "required": ["query"],
            },
        )

    async def execute(self, arguments: dict[str, Any]) -> str:
        query = str(arguments.get("query", "")).strip()
        if not query:
            return "No query provided."
        raw_top_k = arguments.get("top_k")
        top_k = raw_top_k if isinstance(raw_top_k, int) else None
        results = await self._retriever.search(query, top_k=top_k)
        if not results:
            return "No relevant context found."
        return "\n\n".join(
            f"[{i + 1}] (score={r.score:.3f}, source={r.chunk.source}) {r.chunk.text}"
            for i, r in enumerate(results)
        )
