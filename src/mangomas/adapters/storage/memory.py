"""File-backed implementation of the MemoryRepository protocol."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from mangomas.config import MemorySettings

logger = logging.getLogger(__name__)


class FileMemoryRepository:
    """Stores episodic entries as dated flat files and maintains an index document.

    All file I/O is dispatched via :func:`asyncio.to_thread` so the event loop
    is never blocked by disk operations.

    Parameters
    ----------
    settings:
        ``MemorySettings`` instance that provides ``memory_dir`` and
        ``index_file`` configuration.  No hard-coded paths.
    """

    def __init__(self, settings: MemorySettings) -> None:
        self._root = Path(settings.memory_dir)
        self._index_path = self._root / settings.index_file
        self._closed = False

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _episodic_path(self, *, prefix: str = "") -> Path:
        # UTC for stable filenames across timezones / cloud regions.
        today = datetime.now(UTC).date().isoformat()
        stem = f"{prefix}-{today}" if prefix else today
        return self._root / f"{stem}.md"

    def _ensure_dir(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)

    # ── MemoryRepository protocol ─────────────────────────────────────────────

    async def write_episodic(self, content: str, *, prefix: str = "") -> str:
        """Append *content* to today's episodic file; return the resolved path string."""
        path = self._episodic_path(prefix=prefix)

        def _write() -> str:
            self._ensure_dir()
            with path.open("a", encoding="utf-8") as fh:
                fh.write(content)
                if not content.endswith("\n"):
                    fh.write("\n")
            logger.debug("Wrote episodic entry to %s", path)
            return str(path)

        return await asyncio.to_thread(_write)

    async def read_index(self) -> str:
        """Return the index document contents, or an empty string if absent."""

        def _read() -> str:
            if not self._index_path.exists():
                return ""
            return self._index_path.read_text(encoding="utf-8")

        return await asyncio.to_thread(_read)

    async def append_index(self, entry: str) -> None:
        """Append *entry* as a new line to the index document."""

        def _append() -> None:
            self._ensure_dir()
            with self._index_path.open("a", encoding="utf-8") as fh:
                fh.write(entry)
                if not entry.endswith("\n"):
                    fh.write("\n")
            logger.debug("Appended entry to index %s", self._index_path)

        await asyncio.to_thread(_append)

    def close(self) -> None:
        """Mark the repository as closed (no-op for file I/O; provided for protocol compliance)."""
        self._closed = True
