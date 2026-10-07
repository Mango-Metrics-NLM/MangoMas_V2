"""Document parser adapters (spec-0035)."""

from __future__ import annotations

from mangomas.adapters.parsers._auth import GoogleIdTokenProvider, IdTokenProvider
from mangomas.adapters.parsers.base import PARSER_EXTRAS_KEY, DocumentParser, ParsedDocument
from mangomas.adapters.parsers.docling_serve import DoclingServeParser

__all__ = [
    "PARSER_EXTRAS_KEY",
    "DoclingServeParser",
    "DocumentParser",
    "GoogleIdTokenProvider",
    "IdTokenProvider",
    "ParsedDocument",
]
