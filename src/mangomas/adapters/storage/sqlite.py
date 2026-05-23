"""SQLite repository for conversation turns.

Stdlib ``sqlite3`` is synchronous; we wrap writes in ``asyncio.to_thread`` so the
repository satisfies the async surface the orchestrator expects.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mangomas.core.agent import AgentRequest, AgentResponse
from mangomas.errors import PersistenceError

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
        # Serialise all access to the single shared connection.  ``sqlite3``
        # supports ``check_same_thread=False`` but is not safe for concurrent
        # writes/reads on one connection.
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(self._SCHEMA)
            self._conn.commit()
        logger.debug("SQLiteRepository initialised", extra={"db_path": self._path})

    async def save_turn(
        self,
        agent: str,
        request: AgentRequest,
        response: AgentResponse,
    ) -> int:
        """Persist a single turn; returns the new row id."""

        def _write() -> int:
            with self._lock:
                try:
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
                    row_id = int(cur.lastrowid or 0)
                except sqlite3.Error as exc:
                    logger.exception(
                        "save_turn failed",
                        extra={"agent": agent, "db_path": self._path},
                    )
                    raise PersistenceError(str(exc)) from exc
            logger.debug(
                "save_turn ok",
                extra={"row_id": row_id, "agent": agent, "db_path": self._path},
            )
            return row_id

        return await asyncio.to_thread(_write)

    async def list_turns(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent turns, newest first."""

        def _read() -> list[dict[str, Any]]:
            with self._lock:
                try:
                    cur = self._conn.execute(
                        "SELECT id, ts, agent, request, response "
                        "FROM turns ORDER BY id DESC LIMIT ?",
                        (limit,),
                    )
                    rows = cur.fetchall()
                except sqlite3.Error as exc:
                    logger.exception(
                        "list_turns failed",
                        extra={"db_path": self._path},
                    )
                    raise PersistenceError(str(exc)) from exc
            logger.debug(
                "list_turns ok",
                extra={"count": len(rows), "db_path": self._path},
            )
            return [
                {
                    "id": row[0],
                    "ts": row[1],
                    "agent": row[2],
                    "request": json.loads(row[3]),
                    "response": json.loads(row[4]),
                }
                for row in rows
            ]

        return await asyncio.to_thread(_read)

    def close(self) -> None:
        """Close the underlying connection."""
        with self._lock:
            self._conn.close()
        logger.debug("SQLiteRepository closed", extra={"db_path": self._path})
