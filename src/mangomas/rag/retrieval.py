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

import html
import logging
import re
from typing import TYPE_CHECKING, Any, Final

from opentelemetry import trace

from mangomas.core.tools import ToolEffects, ToolSpec
from mangomas.rag.loader import META_PARSE_STATUS
from mangomas.rag.models import Chunk, SearchResult

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.adapters.embeddings.base import EmbeddingClient
    from mangomas.adapters.vector.base import VectorMatch, VectorStoreRepository

__all__ = ["UNTRUSTED_DOCUMENT_TAG", "RetrievalTool", "Retriever", "frame_untrusted"]

logger = logging.getLogger(__name__)

_TOOL_NAME = "retrieve"

# Parser-derived passages are wrapped in this element so the model is told the
# text is untrusted data, not instructions (spec-0035 R9 / ADR-0036).
UNTRUSTED_DOCUMENT_TAG: Final[str] = "untrusted-document"
# Any spelling of the wrapper's own opening or closing tag inside a passage —
# including whitespace before the name and any case — so content can neither
# close the wrapper early nor forge a new one.
_WRAPPER_TOKEN: Final[re.Pattern[str]] = re.compile(
    rf"<(\s*/?\s*){re.escape(UNTRUSTED_DOCUMENT_TAG)}", re.IGNORECASE
)


def frame_untrusted(text: str, source: str) -> str:
    """Wrap a parser-derived passage so it cannot escape its delimiter.

    The ``source`` attribute is HTML-escaped (quotes included); inside the body
    only the ``<`` that opens a wrapper tag is escaped, so ordinary content —
    Markdown tables, comparison operators, other tags — is passed unchanged.
    """
    body = _WRAPPER_TOKEN.sub(lambda m: f"&lt;{m.group(1)}{UNTRUSTED_DOCUMENT_TAG}", text)
    return (
        f'<{UNTRUSTED_DOCUMENT_TAG} source="{_escape_attribute(source)}">'
        f"{body}</{UNTRUSTED_DOCUMENT_TAG}>"
    )


def _escape_attribute(value: str) -> str:
    """HTML-escape *value* and encode line breaks so it stays on one line."""
    return html.escape(value, quote=True).replace("\r", "&#13;").replace("\n", "&#10;")


def _render_passage(chunk: Chunk) -> str:
    """Text-file passages render exactly as before; parsed ones are framed."""
    if _is_parsed(chunk):
        return frame_untrusted(chunk.text, chunk.source)
    return chunk.text


def _is_parsed(chunk: Chunk) -> bool:
    # ``parse_status`` is written on every parser-derived chunk, whatever the
    # caller passed as ``parser_name`` — framing must not hinge on an
    # optional argument.
    return META_PARSE_STATUS in chunk.metadata


def _header_source(chunk: Chunk) -> str:
    """Source shown in the result header; escaped for parser-derived chunks.

    The header sits outside the untrusted frame, so a crafted file name with
    a line break or a closing tag must not be able to forge a result line.
    Text-file sources render exactly as before.
    """
    return _escape_attribute(chunk.source) if _is_parsed(chunk) else chunk.source


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
        """Return up to ``top_k`` (default the configured depth) results for ``query``.

        Instrumented as the query-side counterpart to
        :meth:`~mangomas.rag.pipeline.IngestionPipeline.ingest`. Both halves of
        RAG fail the same silent way: a store that was never populated, a
        source that was dropped at ingest, and a genuinely unrelated query all
        produce zero results and no explanation. The span carries the shape of
        the search; the zero-result case warns, because that is the one a user
        actually reports.

        The query text itself is never logged — it is end-user content, and the
        RAG layer has no way to know whether it carries anything sensitive.
        Only its length is recorded.
        """
        k = self._top_k if top_k is None else max(1, top_k)
        with trace.get_tracer(__name__).start_as_current_span("rag.search") as span:
            span.set_attribute("rag.top_k", k)
            span.set_attribute("rag.query_chars", len(query))
            embedding = await self._embeddings.embed(query)
            matches = await self._vector_store.query(embedding=embedding, top_k=k)
            span.set_attribute("rag.matches", len(matches))
            results = [_match_to_result(m) for m in matches]
            if not results:
                logger.warning(
                    "Retrieval returned no matches",
                    extra={"event": "rag_search_empty", "top_k": k, "query_chars": len(query)},
                )
            else:
                span.set_attribute("rag.top_score", results[0].score)
                logger.debug(
                    "Retrieval completed",
                    extra={
                        "event": "rag_search_completed",
                        "top_k": k,
                        "query_chars": len(query),
                        "matches": len(results),
                        "top_score": results[0].score,
                        "sources": sorted({r.chunk.source for r in results}),
                    },
                )
            return results


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
            # This tool reads the vector store and writes nothing. Declared
            # rather than left UNDECLARED because a vocabulary no tool ever
            # uses is the unwired-control pattern (ADR-0033).
            effects=ToolEffects.READ_ONLY,
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
            # The model emitted a `retrieve` call with no query. Returning the
            # string alone tells the model but not the operator, and this is a
            # prompt/schema problem worth seeing in the logs.
            logger.warning(
                "Retrieval tool called without a query",
                extra={"event": "rag_tool_missing_query", "argument_keys": sorted(arguments)},
            )
            return "No query provided."
        raw_top_k = arguments.get("top_k")
        top_k = raw_top_k if isinstance(raw_top_k, int) else None
        results = await self._retriever.search(query, top_k=top_k)
        if not results:
            return "No relevant context found."
        return "\n\n".join(
            f"[{i + 1}] (score={r.score:.3f}, source={_header_source(r.chunk)}) "
            f"{_render_passage(r.chunk)}"
            for i, r in enumerate(results)
        )
