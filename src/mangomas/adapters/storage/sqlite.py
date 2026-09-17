"""SQLite repository for conversation turns.

Stdlib ``sqlite3`` is synchronous; we wrap writes in ``asyncio.to_thread`` so the
repository satisfies the async surface the orchestrator expects.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mangomas.adapters.storage._schema import (
    TURN_RECORD_COLUMNS,
    TURN_SCHEMA_VERSION,
    TURN_SELECT_COLUMNS,
    TurnStatus,
    sqlite_column_ddl,
    turn_row_to_dict,
)
from mangomas.adapters.storage._url import path_from_sqlite_url
from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE, DEFAULT_STORAGE_LIST_TURNS_LIMIT
from mangomas.core.agent import AgentRequest, AgentResponse
from mangomas.errors import PersistenceError
from mangomas.tenancy import DEFAULT_TENANT, get_tenant

logger = logging.getLogger(__name__)


# Re-exported for backwards compatibility: the real implementation now lives
# in ``_url.py`` so the eval ``sqlite_results`` sink can share it without
# importing the full repository module.
_path_from_url = path_from_sqlite_url


class SQLiteRepository:
    """Persists conversation turns. One row per dispatch."""

    _SCHEMA = f"""
    CREATE TABLE IF NOT EXISTS turns (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        ts        TEXT    NOT NULL,
        agent     TEXT    NOT NULL,
        request   TEXT    NOT NULL,
        response  TEXT    NOT NULL,
        tenant    TEXT    NOT NULL DEFAULT '{DEFAULT_TENANT}'
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
            self._ensure_record_columns()
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
                f"ALTER TABLE turns ADD COLUMN tenant TEXT NOT NULL DEFAULT '{DEFAULT_TENANT}'"
            )

    def _existing_columns(self) -> set[str]:
        """Return the ``turns`` table's current column names. Called under ``self._lock``."""
        return {row[1] for row in self._conn.execute("PRAGMA table_info(turns)")}

    def _ensure_record_columns(self) -> None:
        """Idempotently append any missing :data:`TURN_RECORD_COLUMNS`.

        Additive only — never drops or alters — so a database written by an
        older build keeps working and its rows adopt each column's default.
        Driven from the shared column tuple rather than a hand-written list, so
        this backend cannot fall behind the Postgres one. Called under
        ``self._lock``.
        """
        columns = self._existing_columns()
        for column in TURN_RECORD_COLUMNS:
            if column.name in columns:
                continue
            self._conn.execute(f"ALTER TABLE turns ADD COLUMN {sqlite_column_ddl(column)}")
            logger.info(
                "turns table migrated",
                extra={
                    "event": "turn_schema_migrated",
                    "column": column.name,
                    "schema_version": TURN_SCHEMA_VERSION,
                    "db_path": self._path,
                },
            )

    def _insert_turn(
        self,
        *,
        agent: str,
        request_json: str,
        response_json: str,
        tenant: str,
        status: TurnStatus,
        error_code: str | None,
        error: str | None,
        operation: str,
    ) -> int:
        """Write one row and return its id. Shared by the success and failure paths.

        One INSERT statement for both outcomes, so a column added to the record
        cannot reach only one of them — the asymmetry that let failures go
        unrecorded in the first place.
        """
        with self._lock:
            try:
                cur = self._conn.execute(
                    "INSERT INTO turns "
                    "(ts, agent, request, response, tenant, schema_version, status, "
                    "error_code, error) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        datetime.now(UTC).isoformat(),
                        agent,
                        request_json,
                        response_json,
                        tenant,
                        TURN_SCHEMA_VERSION,
                        str(status),
                        error_code,
                        error,
                    ),
                )
                self._conn.commit()
                row_id = int(cur.lastrowid or 0)
            except sqlite3.Error as exc:
                logger.exception(
                    "%s failed",
                    operation,
                    extra={"agent": agent, "db_path": self._path},
                )
                raise PersistenceError(
                    "Failed to persist turn",
                    detail=f"{type(exc).__name__}: {exc}"[:DEFAULT_ERROR_DETAIL_TRUNCATE],
                ) from exc
        logger.debug(
            "%s ok",
            operation,
            extra={
                "row_id": row_id,
                "agent": agent,
                "status": str(status),
                "db_path": self._path,
            },
        )
        return row_id

    async def save_failed_turn(
        self,
        agent: str,
        request: AgentRequest,
        *,
        error_code: str,
        error: str,
    ) -> int:
        """Persist a dispatch that raised; returns the new row id.

        ``response`` is stored as an empty JSON object rather than NULL: the
        column is ``NOT NULL`` on every existing database, and a failure has no
        response by definition. The ``status`` column, not a sentinel in the
        payload, is what distinguishes the two.
        """
        tenant = get_tenant()
        truncated = error[:DEFAULT_ERROR_DETAIL_TRUNCATE]

        def _write() -> int:
            return self._insert_turn(
                agent=agent,
                request_json=request.model_dump_json(),
                response_json="{}",
                tenant=tenant,
                status=TurnStatus.ERROR,
                error_code=error_code,
                error=truncated,
                operation="save_failed_turn",
            )

        return await asyncio.to_thread(_write)

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
            return self._insert_turn(
                agent=agent,
                request_json=request.model_dump_json(),
                response_json=response.model_dump_json(),
                tenant=tenant,
                status=TurnStatus.OK,
                error_code=None,
                error=None,
                operation="save_turn",
            )

        return await asyncio.to_thread(_write)

    async def list_turns(
        self, limit: int = DEFAULT_STORAGE_LIST_TURNS_LIMIT
    ) -> list[dict[str, Any]]:
        """Return the most recent turns for the active tenant, newest first."""
        tenant = get_tenant()

        def _read() -> list[dict[str, Any]]:
            with self._lock:
                try:
                    cur = self._conn.execute(
                        f"SELECT {', '.join(TURN_SELECT_COLUMNS)} "  # noqa: S608
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
            return [turn_row_to_dict(row) for row in rows]

        return await asyncio.to_thread(_read)

    def close(self) -> None:
        """Close the underlying connection."""
        with self._lock:
            self._conn.close()
        logger.debug("SQLiteRepository closed", extra={"db_path": self._path})
