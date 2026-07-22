"""Postgres repository for conversation turns (asyncpg-backed).

Mirrors the shape of :class:`~mangomas.adapters.storage.sqlite.SQLiteRepository`
but does not need a ``threading.Lock`` — asyncpg's connection pool is
natively async and per-connection-safe.

The pool is created lazily on the first ``save_turn`` / ``list_turns``
call so the constructor is sync and matches the existing
``_sqlite_factory(cfg: DBSettings) -> SQLiteRepository`` shape in
:mod:`mangomas.composition`. The asyncpg SDK import is deferred to
function bodies so this module is importable even when the optional
``postgres`` extra is not installed.

JSONB columns: asyncpg returns ``jsonb`` values as ``str`` by default,
which would break protocol parity with the SQLite repo (which returns
``dict``). The pool's ``init`` hook registers a ``json.dumps`` /
``json.loads`` codec on every connection to keep ``list_turns`` row
shapes interchangeable.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE, DBSettings
from mangomas.core.agent import AgentRequest, AgentResponse
from mangomas.errors import PersistenceError
from mangomas.tenancy import get_tenant

if TYPE_CHECKING:  # pragma: no cover
    import asyncpg

logger = logging.getLogger(__name__)


_PG_SCHEMA: str = """
CREATE TABLE IF NOT EXISTS turns (
    id        BIGSERIAL    PRIMARY KEY,
    ts        TIMESTAMPTZ  NOT NULL,
    agent     TEXT         NOT NULL,
    request   JSONB        NOT NULL,
    response  JSONB        NOT NULL,
    tenant    TEXT         NOT NULL DEFAULT 'default'
);
"""

# Idempotent migration for a table created before multi-tenancy (ADR-0017);
# existing rows adopt the column default. No-op on a fresh table.
_PG_TENANT_MIGRATION: str = (
    "ALTER TABLE turns ADD COLUMN IF NOT EXISTS tenant TEXT NOT NULL DEFAULT 'default'"
)


def _normalise_dsn(url: str) -> str:
    """Coerce a Postgres DSN into the form asyncpg accepts.

    asyncpg requires ``postgresql://`` (or ``postgres://`` is also tolerated
    by newer versions, but ``postgresql://`` is canonical). This helper:

    * rejects empty/whitespace-only input with :class:`ValueError`,
    * rewrites a leading ``postgres://`` to ``postgresql://`` so older
      callers using the legacy scheme keep working,
    * passes everything else through unchanged.
    """
    if not url or not url.strip():
        raise ValueError("Empty Postgres DSN")
    stripped = url.strip()
    if stripped.startswith("postgres://"):
        return "postgresql://" + stripped[len("postgres://") :]
    return stripped


def _dsn_host(url: str) -> str | None:
    """Extract the host component of a DSN for structured logging.

    Returns ``None`` if the URL does not parse — never raises. The full
    DSN is **never** logged because it may carry a password in userinfo.
    """
    try:
        return urlparse(url).hostname
    except (ValueError, AttributeError):  # pragma: no cover  -- urlparse is very lenient
        return None


class PostgresRepository:
    """Async Postgres-backed :class:`TurnRepository` using an asyncpg pool.

    The pool is created on first use (``_ensure_pool``) to keep the
    constructor cheap and synchronous — matching the existing
    ``DBSettings -> repo`` factory contract.
    """

    def __init__(self, cfg: DBSettings) -> None:
        self._dsn: str = _normalise_dsn(cfg.url)
        self._pool_min: int = cfg.pool_min
        self._pool_max: int = cfg.pool_max
        self._connect_timeout: float = cfg.connect_timeout_seconds
        self._statement_timeout: float | None = cfg.statement_timeout_seconds
        self._pool: asyncpg.Pool | None = None
        # Single async lock guards lazy pool creation only — never per-query.
        self._init_lock: asyncio.Lock = asyncio.Lock()

    async def _ensure_pool(self) -> asyncpg.Pool:
        """Create the asyncpg pool on first call (idempotent, async-safe)."""
        if self._pool is not None:
            return self._pool
        async with self._init_lock:
            if self._pool is not None:  # pragma: no cover  -- race coverage
                return self._pool
            import asyncpg  # noqa: PLC0415

            async def _init_codecs(conn: asyncpg.Connection) -> None:
                # Decode jsonb columns into dicts to match SQLiteRepository's
                # row shape (sqlite.py uses json.loads on TEXT columns).
                await conn.set_type_codec(
                    "jsonb",
                    encoder=json.dumps,
                    decoder=json.loads,
                    schema="pg_catalog",
                )

            logger.info(
                "Creating asyncpg pool",
                extra={
                    "dsn_host": _dsn_host(self._dsn),
                    "pool_min": self._pool_min,
                    "pool_max": self._pool_max,
                },
            )
            self._pool = await asyncpg.create_pool(
                self._dsn,
                min_size=self._pool_min,
                max_size=self._pool_max,
                timeout=self._connect_timeout,
                init=_init_codecs,
            )
            async with self._pool.acquire() as conn:
                if self._statement_timeout is not None:
                    # statement_timeout is in milliseconds.
                    ms = int(self._statement_timeout * 1000)
                    await conn.execute(f"SET statement_timeout = {ms}")
                await conn.execute(_PG_SCHEMA)
                await conn.execute(_PG_TENANT_MIGRATION)
            return self._pool

    async def save_turn(
        self,
        agent: str,
        request: AgentRequest,
        response: AgentResponse,
    ) -> int:
        """Persist a single turn; returns the new row id."""
        import asyncpg  # noqa: PLC0415

        tenant = get_tenant()
        pool = await self._ensure_pool()
        try:
            async with pool.acquire() as conn:
                row_id = await conn.fetchval(
                    "INSERT INTO turns (ts, agent, request, response, tenant) "
                    "VALUES ($1, $2, $3, $4, $5) RETURNING id",
                    datetime.now(UTC),
                    agent,
                    request.model_dump_json(),
                    response.model_dump_json(),
                    tenant,
                )
            int_id = int(row_id)
            logger.debug(
                "Postgres save_turn persisted",
                extra={
                    "row_id": int_id,
                    "agent": agent,
                    "dsn_host": _dsn_host(self._dsn),
                },
            )
            return int_id
        except asyncpg.PostgresError as exc:
            logger.error(
                "Postgres save_turn failed",
                extra={"error": type(exc).__name__, "dsn_host": _dsn_host(self._dsn)},
            )
            raise PersistenceError(
                "Failed to persist turn",
                detail=f"{type(exc).__name__}: {exc}"[:DEFAULT_ERROR_DETAIL_TRUNCATE],
            ) from exc

    async def list_turns(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent turns for the active tenant, newest first."""
        import asyncpg  # noqa: PLC0415

        tenant = get_tenant()
        pool = await self._ensure_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, ts, agent, request, response FROM turns "
                    "WHERE tenant = $1 ORDER BY id DESC LIMIT $2",
                    tenant,
                    limit,
                )
        except asyncpg.PostgresError as exc:
            logger.error(
                "Postgres list_turns failed",
                extra={"error": type(exc).__name__, "dsn_host": _dsn_host(self._dsn)},
            )
            raise PersistenceError(
                "Failed to list turns",
                detail=f"{type(exc).__name__}: {exc}"[:DEFAULT_ERROR_DETAIL_TRUNCATE],
            ) from exc
        return [
            {
                "id": int(row["id"]),
                "ts": row["ts"].isoformat() if row["ts"] is not None else None,
                "agent": row["agent"],
                "request": row["request"],
                "response": row["response"],
            }
            for row in rows
        ]

    async def aclose(self) -> None:
        """Close the asyncpg pool cleanly (idempotent)."""
        if self._pool is None:
            return
        await self._pool.close()
        self._pool = None

    def close(self) -> None:
        """Best-effort synchronous close.

        The FastAPI lifespan and CLI commands prefer :meth:`aclose` via the
        :class:`AsyncCloseableRepository` extension protocol. This sync
        fallback is here for callers that never enter an event loop and
        simply needs to release native socket resources at process exit;
        it terminates connections without flushing in-flight queries.
        """
        if self._pool is None:
            return
        try:
            self._pool.terminate()
        except Exception as exc:  # pragma: no cover  -- defensive
            # Log only the exception *type* — never the body. asyncpg
            # exceptions can in some failure modes embed connection-URL
            # text in the message; ``dsn_host`` is the only DSN-derived
            # field we ever emit.
            logger.warning(
                "Postgres pool terminate raised",
                extra={"error": type(exc).__name__, "dsn_host": _dsn_host(self._dsn)},
            )
        self._pool = None
