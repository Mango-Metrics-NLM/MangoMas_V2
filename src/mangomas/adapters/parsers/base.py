"""DocumentParser Protocol — the document-conversion surface RAG ingestion depends on.

Kept on primitives (``filename`` / ``content: bytes`` in, a :class:`ParsedDocument`
of ``str`` / ``int`` / ``bool`` out) so a parser adapter never imports ``rag/``;
the ingestion layer turns a :class:`ParsedDocument` into its own ``RawDoc``. This
mirrors :mod:`mangomas.adapters.vector.base` and preserves the
``adapters → core`` dependency direction (spec-0035 R1 / ADR-0036).

Parsed text is **untrusted data**: a parser converts operator-supplied binaries
and its output must never be treated as instructions downstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

# Key under which composition attaches the configured parser to
# ``AgentContext.extras`` (ADR-0036 §10). A constant, so the producer and the
# ``rag ingest`` consumer cannot disagree on its spelling.
PARSER_EXTRAS_KEY: Final[str] = "document_parser"


@dataclass(frozen=True)
class ParsedDocument:
    """The text a parser extracted from one document.

    ``text`` is the converted Markdown. ``pages`` is the page count when the
    parser reports one (``None`` otherwise). ``partial`` is ``True`` when the
    parser converted only part of the document (e.g. a page cap or a
    per-page failure) — the text is usable but incomplete.
    """

    text: str
    pages: int | None = None
    partial: bool = False


@runtime_checkable
class DocumentParser(Protocol):
    """Convert one binary document to text."""

    async def parse(self, *, filename: str, content: bytes) -> ParsedDocument:
        """Return the text of ``content``; ``filename`` carries its format suffix.

        Implementations translate every transport or upstream failure into a
        typed :class:`~mangomas.errors.MangomasError`; no raw client exception
        escapes.
        """
        ...

    async def aclose(self) -> None:
        """Release any held resources (e.g. an HTTP client); safe to call twice."""
        ...
