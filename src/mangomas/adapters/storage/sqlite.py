"""SQLite repository for conversation turns.

Stdlib ``sqlite3`` is synchronous; we wrap writes in ``asyncio.to_thread`` so the
repository satisfies the async surface the orchestrator expects.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mangomas.core.agent import AgentRequest, AgentResponse

logger = logging.getLogger(__name__)


def _path_from_url(url: str) -> str:
    """Parse ``sqlite:///path`` URLs; fall back to bare paths or ``:memory:``.

    ``sqlite:///abs/path.db`` -> ``abs/path.db`` (absolute-style, three slashes).
    ``sqlite://relative.db``  -> ``relative.db`` (netloc form, two slashes).
    ``:memory:`` / ``file::memory:...`` -> ``:memory:``.
    Anything else is treated as a bare filesystem path.
    """
    if url == ":memory:" or url.startswith("file::memory:"):
        return ":memory:"
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///") :]
    if url.startswith("sqlite://"):
        parsed = urlparse(url)
        # netloc carries the path when only two slashes are present.
        path = parsed.path.lstrip("/")
        combined = f"{parsed.netloc}/{path}" if path else parsed.netloc
        if not combined:
            raise ValueError(f"Empty sqlite URL path: {url!r}")
        return combined
    return url


class SQLiteRepository:
    """Persists conversation turns. One row per dispatch."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS turns (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        ts        TEXT    NOT NULL,
        agent     TEXT    NOT NULL,
        request   TEXT    NOT NULL,
        response  TEXT    NOT NULL
    );
    """

    def __init__(self, url: str) -> None:
        self._path = _path_from_url(url)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute(self._SCHEMA)
        self._conn.commit()

    async def save_turn(
        self,
        agent: str,
        request: AgentRequest,
        response: AgentResponse,
    ) -> int:
        """Persist a single turn; returns the new row id."""

        def _write() -> int:
            cur = self._conn.execute(
                "INSERT INTO turns (ts, agent, request, response) VALUES (?, ?, ?, ?)",
                (
                    datetime.now(UTC).isoformat(),
                    agent,
                    request.model_dump_json(),
                    response.model_dump_json(),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

        return await asyncio.to_thread(_write)

    async def list_turns(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent turns, newest first."""

        def _read() -> list[dict[str, Any]]:
            cur = self._conn.execute(
                "SELECT id, ts, agent, request, response FROM turns ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            return [
                {
                    "id": row[0],
                    "ts": row[1],
                    "agent": row[2],
                    "request": json.loads(row[3]),
                    "response": json.loads(row[4]),
                }
                for row in cur.fetchall()
            ]

        return await asyncio.to_thread(_read)

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()
