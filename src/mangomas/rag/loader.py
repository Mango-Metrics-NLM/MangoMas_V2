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
from dataclasses import dataclass
from pathlib import Path

from mangomas.errors import ConfigError

__all__ = ["RawDoc", "load_documents"]

# Extensions treated as ingestable plain text when scanning a directory.
_TEXT_SUFFIXES: frozenset[str] = frozenset({".txt", ".md"})


@dataclass(frozen=True)
class RawDoc:
    """An unchunked source document: its stable ``source`` id and raw ``text``."""

    source: str
    text: str


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
                pass
        return docs
    try:
        text = root.read_text(encoding="utf-8")
        return [RawDoc(source=root.as_posix(), text=text)]
    except UnicodeDecodeError:
        return []
