"""Document loading for the RAG ingestion pipeline.

Reads UTF-8 text from a single file or, when given a directory, every ``*.txt``
and ``*.md`` file beneath it (recursively, sorted for deterministic ordering).
All filesystem access runs inside :func:`asyncio.to_thread` so the async
ingestion path never blocks the event loop (CLAUDE.md async-I/O rule).

The ``source`` of each :class:`RawDoc` is stored as a canonical POSIX string:
``Path.as_posix()`` for a single file, or the POSIX relative path beneath the
loaded directory. That stable identifier is written into chunk metadata so
re-ingestion can target it for deletion.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from mangomas.errors import ConfigError, DocumentParseError, MangomasError

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.adapters.parsers.base import DocumentParser
    from mangomas.config.parser import ParserSettings

logger = logging.getLogger(__name__)

__all__ = [
    "META_PARSER",
    "META_PARSE_STATUS",
    "PARSE_STATUS_PARTIAL",
    "PARSE_STATUS_SUCCESS",
    "ParseFailure",
    "RawDoc",
    "iter_documents",
    "load_documents",
]

# Extensions treated as ingestable plain text when scanning a directory.
_TEXT_SUFFIXES: frozenset[str] = frozenset({".txt", ".md"})


# Chunk-metadata keys written for parser-derived documents only (spec-0035 R8).
# Text documents carry no metadata, so their chunks stay ``{source, index}``.
META_PARSER: Final[str] = "parser"
META_PARSE_STATUS: Final[str] = "parse_status"
PARSE_STATUS_SUCCESS: Final[str] = "success"
PARSE_STATUS_PARTIAL: Final[str] = "partial"

# Span status attribute values for ``rag.parse``.
_SPAN_STATUS_FAILED: Final[str] = "failed"


def _empty_metadata() -> Mapping[str, Any]:
    return MappingProxyType({})


@dataclass(frozen=True)
class RawDoc:
    """An unchunked source document: its stable ``source`` id and raw ``text``.

    ``metadata`` is empty for text files and holds the parser name and parse
    status for parser-derived documents. It is an immutable mapping, so a
    default instance can never be mutated into another document's metadata.
    """

    source: str
    text: str
    # ``hash=False``: a mapping is unhashable, and RawDoc was hashable before
    # this field existed — equality still compares it.
    metadata: Mapping[str, Any] = field(default_factory=_empty_metadata, hash=False)


@dataclass(frozen=True)
class ParseFailure:
    """A document the parser could not turn into text (spec-0035 R6).

    Yielded by :func:`iter_documents` instead of raising, so the caller decides
    between skipping it and failing the run — and, crucially, so a failed
    parse never reaches the code that replaces a source's stored vectors.
    """

    source: str
    error: MangomasError


async def load_documents(path: str) -> list[RawDoc]:
    """Load one document (file) or many (directory) as :class:`RawDoc` objects.

    Args:
        path: A file path or a directory to scan for ``*.txt`` / ``*.md`` files.

    Returns:
        Documents in deterministic (sorted-by-source) order.

    Raises:
        ConfigError: ``path`` does not exist.
    """
    return await asyncio.to_thread(_load_documents_sync, path)


def _load_documents_sync(path: str) -> list[RawDoc]:
    root = Path(path)
    if not root.exists():
        raise ConfigError(f"Ingestion path does not exist: {path!r}", detail=f"path={path!r}")
    if root.is_dir():
        files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in _TEXT_SUFFIXES)
        docs = []
        for p in files:
            try:
                text = p.read_text(encoding="utf-8")
                docs.append(RawDoc(source=p.relative_to(root).as_posix(), text=text))
            except UnicodeDecodeError:
                logger.warning("Skipped non-UTF-8 binary file: %s", p)
                continue
        return docs
    try:
        text = root.read_text(encoding="utf-8")
        return [RawDoc(source=root.as_posix(), text=text)]
    except UnicodeDecodeError:
        logger.warning("Skipped non-UTF-8 binary file: %s", root)
        return []


async def iter_documents(
    path: str,
    *,
    parser: DocumentParser | None = None,
    settings: ParserSettings | None = None,
    parser_name: str | None = None,
) -> AsyncGenerator[RawDoc | ParseFailure, None]:
    """Yield documents one at a time, parsing non-text files when a parser is given.

    Text files (``*.txt`` / ``*.md``) follow :func:`load_documents`' rules
    exactly — same decoding, same skip-and-warn for non-UTF-8 bytes, same
    ``source`` ids — so with ``parser=None`` this yields what
    :func:`load_documents` returns, in the same order. Files whose suffix is in
    ``settings.allowed_suffixes`` are sent to ``parser`` one at a time, so at
    most one document is held in memory.

    Differences from :func:`load_documents`, which is left unchanged:

    * In directory mode a file whose resolved path leaves the ingest root (a
      symlink pointing outside it) is skipped with a ``rag_symlink_escape``
      warning, because parsed files may be uploaded to another service.
    Source ids follow :func:`load_documents` for every file: the POSIX path
    relative to a directory, or the path exactly as given for a single file
    (a bare name would make two same-named files overwrite each other's
    vectors). Pass a relative path to keep absolute paths out of chunk
    metadata.

    Parse problems are yielded as :class:`ParseFailure` — an oversize file (not
    read, parser not called), a :class:`~mangomas.errors.DocumentParseError`,
    or empty text from a non-empty file. A
    :class:`~mangomas.errors.ConfigError` propagates: a misconfigured parser
    should fail the run, not quietly skip every file.

    Raises:
        ConfigError: ``path`` does not exist (before anything is yielded).
    """
    root = Path(path)
    if not await asyncio.to_thread(root.exists):
        raise ConfigError(f"Ingestion path does not exist: {path!r}", detail=f"path={path!r}")
    parsed_suffixes = _parsed_suffixes(parser, settings)
    if await asyncio.to_thread(root.is_dir):
        candidates = await asyncio.to_thread(_directory_candidates, root, parsed_suffixes)
    else:
        candidates = [(root, root.as_posix())]
    single_file = len(candidates) == 1 and candidates[0][0] == root
    for file_path, source in candidates:
        if file_path.suffix not in _TEXT_SUFFIXES and file_path.suffix.lower() in parsed_suffixes:
            # ``parsed_suffixes`` is non-empty only when both are present.
            assert parser is not None and settings is not None  # noqa: S101
            yield await _parse_document(file_path, source, parser, settings, parser_name)
        elif file_path.suffix in _TEXT_SUFFIXES or single_file:
            # A single named file is read as text whatever its suffix, exactly
            # as ``load_documents`` does (``rag ingest notes.rst`` keeps working
            # when a parser is enabled).
            doc = await asyncio.to_thread(_read_text_document, file_path, source)
            if doc is not None:
                yield doc


def _parsed_suffixes(
    parser: DocumentParser | None, settings: ParserSettings | None
) -> frozenset[str]:
    if parser is None or settings is None:
        return frozenset()
    return frozenset(settings.allowed_suffixes)


def _directory_candidates(root: Path, parsed_suffixes: frozenset[str]) -> list[tuple[Path, str]]:
    """Return ``(path, source)`` for every ingestable file, in ``Path`` order."""
    resolved_root = root.resolve()
    found: list[tuple[Path, str]] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in _TEXT_SUFFIXES and p.suffix.lower() not in parsed_suffixes:
            continue
        source = p.relative_to(root).as_posix()
        if not p.resolve().is_relative_to(resolved_root):
            logger.warning(
                "Skipped a file that resolves outside the ingest root",
                extra={"event": "rag_symlink_escape", "source": source},
            )
            continue
        found.append((p, source))
    # Sort by ``Path`` exactly as :func:`load_documents` does: paths compare part
    # by part, which differs from sorting the POSIX strings ("_/_.txt" sorts
    # before "_.txt" here, after it as a string). Parity depends on it.
    found.sort(key=lambda item: item[0])
    return found


def _read_text_document(file_path: Path, source: str) -> RawDoc | None:
    try:
        return RawDoc(source=source, text=file_path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        logger.warning("Skipped non-UTF-8 binary file: %s", file_path)
        return None


async def _parse_document(
    file_path: Path,
    source: str,
    parser: DocumentParser,
    settings: ParserSettings,
    parser_name: str | None,
) -> RawDoc | ParseFailure:
    """Parse one file inside a ``rag.parse`` span; never raises a DocumentParseError."""
    size = (await asyncio.to_thread(file_path.stat)).st_size
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span(
        "rag.parse", record_exception=False, set_status_on_exception=False
    ) as span:
        span.set_attribute("rag.source", source)
        span.set_attribute("rag.parse.bytes", size)
        try:
            if size > settings.max_file_bytes:
                raise DocumentParseError(
                    "document exceeds the parser size limit",
                    source=source,
                    detail=f"bytes={size} max_file_bytes={settings.max_file_bytes}",
                )
            content = await asyncio.to_thread(file_path.read_bytes)
            parsed = await parser.parse(filename=file_path.name, content=content)
            if not parsed.text.strip() and size > 0:
                raise DocumentParseError(
                    "parser returned no text", source=source, detail=f"bytes={size}"
                )
        except DocumentParseError as exc:
            error = _with_source(exc, source)
            span.set_attribute("rag.parse.status", _SPAN_STATUS_FAILED)
            span.set_attribute("error.code", error.code)
            span.set_status(Status(StatusCode.ERROR))
            logger.warning(
                "Document could not be parsed",
                extra={
                    "event": "rag_document_parse_failed",
                    "source": source,
                    "bytes": size,
                    "error_code": error.code,
                },
            )
            return ParseFailure(source=source, error=error)
        status = PARSE_STATUS_PARTIAL if parsed.partial else PARSE_STATUS_SUCCESS
        span.set_attribute("rag.parse.status", status)
        if parsed.pages is not None:
            span.set_attribute("rag.parse.pages", parsed.pages)
        logger.debug(
            "Document parsed",
            extra={
                "event": "rag_document_parsed",
                "source": source,
                "bytes": size,
                "status": status,
            },
        )
        metadata: dict[str, Any] = {META_PARSE_STATUS: status}
        if parser_name:
            metadata[META_PARSER] = parser_name
        return RawDoc(source=source, text=parsed.text, metadata=MappingProxyType(metadata))


def _with_source(exc: DocumentParseError, source: str) -> DocumentParseError:
    """Return *exc* carrying *source*, building a copy rather than mutating it."""
    if exc.source is not None:
        return exc
    copy = DocumentParseError(str(exc), source=source, detail=exc.detail)
    copy.__cause__ = exc
    return copy
