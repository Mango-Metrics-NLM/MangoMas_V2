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

from mangomas.adapters.storage._url import path_from_sqlite_url
from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE
from mangomas.core.agent import AgentRequest, AgentResponse
from mangomas.errors import PersistenceError
from mangomas.tenancy import get_tenant

logger = logging.getLogger(__name__)


# Re-exported for backwards compatibility: the real implementation now lives
# in ``_url.py`` so the eval ``sqlite_results`` sink can share it without
# importing the full repository module.
_path_from_url = path_from_sqlite_url


class SQLiteRepository:
    """Persists conversation turns. One row per dispatch."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS turns (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        ts        TEXT    NOT NULL,
        agent     TEXT    NOT NULL,
        request   TEXT    NOT NULL,
        response  TEXT    NOT NULL,
        tenant    TEXT    NOT NULL DEFAULT 'default'
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
            self._ensure_tenant_column()
            self._conn.commit()
        logger.debug("SQLiteRepository initialised", extra={"db_path": self._path})

    def _ensure_tenant_column(self) -> None:
        """Idempotently add the ``tenant`` column to a pre-tenancy table.

        A fresh table already has it via ``_SCHEMA``; a database created before
        multi-tenancy gets the column via ``ALTER`` (existing rows adopt the
        ``'default'`` column default). Called under ``self._lock``.
        """
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(turns)")}
        if "tenant" not in columns:
            self._conn.execute(
                "ALTER TABLE turns ADD COLUMN tenant TEXT NOT NULL DEFAULT 'default'"
            )

    async def save_turn(
        self,
        agent: str,
        request: AgentRequest,
        response: AgentResponse,
    ) -> int:
        """Persist a single turn; returns the new row id."""
        # Read the active tenant in the async context, then capture it in the
        # worker-thread closure — do NOT read the ContextVar inside the thread.
        tenant = get_tenant()

        def _write() -> int:
            with self._lock:
                try:
                    cur = self._conn.execute(
                        "INSERT INTO turns (ts, agent, request, response, tenant) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            datetime.now(UTC).isoformat(),
                            agent,
                            request.model_dump_json(),
                            response.model_dump_json(),
                            tenant,
                        ),
                    )
                    self._conn.commit()
                    row_id = int(cur.lastrowid or 0)
                except sqlite3.Error as exc:
                    logger.exception(
                        "save_turn failed",
                        extra={"agent": agent, "db_path": self._path},
                    )
                    raise PersistenceError(
                        "Failed to persist turn",
                        detail=f"{type(exc).__name__}: {exc}"[:DEFAULT_ERROR_DETAIL_TRUNCATE],
                    ) from exc
            logger.debug(
                "save_turn ok",
                extra={"row_id": row_id, "agent": agent, "db_path": self._path},
            )
            return row_id

        return await asyncio.to_thread(_write)

    async def list_turns(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent turns for the active tenant, newest first."""
        tenant = get_tenant()

        def _read() -> list[dict[str, Any]]:
            with self._lock:
                try:
                    cur = self._conn.execute(
                        "SELECT id, ts, agent, request, response "
                        "FROM turns WHERE tenant = ? ORDER BY id DESC LIMIT ?",
                        (tenant, limit),
                    )
                    rows = cur.fetchall()
                except sqlite3.Error as exc:
                    logger.exception(
                        "list_turns failed",
                        extra={"db_path": self._path},
                    )
                    raise PersistenceError(
                        "Failed to list turns",
                        detail=f"{type(exc).__name__}: {exc}"[:DEFAULT_ERROR_DETAIL_TRUNCATE],
                    ) from exc
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
