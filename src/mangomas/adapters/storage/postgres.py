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
from types import ModuleType
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from mangomas.config import (
    DEFAULT_ERROR_DETAIL_TRUNCATE,
    DEFAULT_STORAGE_LIST_TURNS_LIMIT,
    DBSettings,
)
from mangomas.core.agent import AgentRequest, AgentResponse
from mangomas.errors import PersistenceError
from mangomas.tenancy import DEFAULT_TENANT, get_tenant

if TYPE_CHECKING:  # pragma: no cover
    import asyncpg

logger = logging.getLogger(__name__)


_PG_SCHEMA: str = f"""
CREATE TABLE IF NOT EXISTS turns (
    id        BIGSERIAL    PRIMARY KEY,
    ts        TIMESTAMPTZ  NOT NULL,
    agent     TEXT         NOT NULL,
    request   JSONB        NOT NULL,
    response  JSONB        NOT NULL,
    tenant    TEXT         NOT NULL DEFAULT '{DEFAULT_TENANT}'
);
"""

# Idempotent migration for a table created before multi-tenancy (ADR-0017);
# existing rows adopt the column default. No-op on a fresh table.
_PG_TENANT_MIGRATION: str = (
    f"ALTER TABLE turns ADD COLUMN IF NOT EXISTS tenant TEXT NOT NULL DEFAULT '{DEFAULT_TENANT}'"
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


def _driver_failures(driver: ModuleType) -> tuple[type[BaseException], ...]:
    """Driver failure modes that must surface as :class:`PersistenceError`.

    ``asyncpg.PostgresError`` alone is **not** the driver's error vocabulary —
    it is only the server-reported half. Verified against the installed
    asyncpg, none of these is a ``PostgresError`` subclass:

    * ``InterfaceError`` — ``acquire()`` on a closing/closed pool, bad bind
      types. Its own root (``InterfaceMessage``).
    * ``InternalClientError`` — driver protocol/state faults. Its own root too.
    * ``OSError`` — the transport: connection refused, reset, and DNS failure
      (``socket.gaierror``). This is how a wrong host reaches us.
    * ``TimeoutError`` — connect budget or pool-acquire exhaustion. An
      ``OSError`` subclass since 3.10, and ``asyncio.TimeoutError`` is an
      alias of it on 3.11; named anyway because it is a distinct failure mode
      an operator will look for.

    ``asyncio.CancelledError`` is deliberately absent: it is a ``BaseException``
    and must propagate, so shutdown is never reported as a persistence fault.

    Takes the ``asyncpg`` module rather than importing it, so this module stays
    importable without the optional ``postgres`` extra and the caller keeps its
    existing fail-fast ``import asyncpg``. The expression is evaluated only
    when an exception is actually being matched, so the happy path pays
    nothing.
    """
    return (
        driver.PostgresError,
        driver.InterfaceError,
        driver.InternalClientError,
        OSError,
        TimeoutError,
    )


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

    def _server_settings(self) -> dict[str, str] | None:
        """Startup-packet parameters applied to **every** pooled connection.

        ``statement_timeout`` used to be issued as ``SET statement_timeout``
        on the single connection borrowed for DDL. That is a *session* GUC, so
        the other ``pool_min..pool_max`` connections never received it — and
        asyncpg's ``Connection.reset()`` sends ``RESET ALL`` when a connection
        is released, discarding it even for that one. The documented
        ``MANGOMAS_DB__STATEMENT_TIMEOUT_SECONDS`` knob was inert.

        asyncpg forwards ``server_settings`` in the startup packet of every
        connection it opens, and ``RESET ALL`` restores a session to its
        *start-up* values — so the timeout both reaches the whole pool and
        survives connection reuse.

        Returns ``None`` when the knob is unset, which is asyncpg's own
        default, so a deployment that never set it is byte-identical.
        """
        if self._statement_timeout is None:
            return None
        # Postgres reads a unit-less statement_timeout as milliseconds.
        return {"statement_timeout": str(int(self._statement_timeout * 1000))}

    async def _ensure_pool(self) -> asyncpg.Pool:
        """Create the asyncpg pool on first call (idempotent, async-safe).

        ``self._pool`` is published **only after** the schema + migration DDL
        succeeds. Assigning it first latched a pool whose table was never
        created: the early return above then handed every later call that same
        schema-less pool, and each query failed on a missing relation until the
        process restarted. On failure the half-built pool is terminated and the
        attribute left ``None``, so the next call retries from scratch.
        """
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
            pool: asyncpg.Pool | None = None
            try:
                pool = await asyncpg.create_pool(
                    self._dsn,
                    min_size=self._pool_min,
                    max_size=self._pool_max,
                    timeout=self._connect_timeout,
                    init=_init_codecs,
                    server_settings=self._server_settings(),
                )
                logger.info(
                    "asyncpg pool created",
                    extra={
                        "dsn_host": _dsn_host(self._dsn),
                        "pool_min": self._pool_min,
                        "pool_max": self._pool_max,
                        "statement_timeout_seconds": self._statement_timeout,
                    },
                )
                async with pool.acquire() as conn:
                    await conn.execute(_PG_SCHEMA)
                    await conn.execute(_PG_TENANT_MIGRATION)
            except BaseException as exc:
                # ``BaseException`` so a cancelled bootstrap also releases the
                # sockets it opened; the exception is always re-raised.
                # Log the exception *type* only — see ``_terminate_quietly``.
                logger.error(
                    "asyncpg pool initialisation failed",
                    extra={
                        "error": type(exc).__name__,
                        "phase": "create_pool" if pool is None else "schema",
                        "dsn_host": _dsn_host(self._dsn),
                    },
                )
                if pool is not None:
                    self._terminate_quietly(pool)
                raise
            logger.info(
                "asyncpg schema applied",
                extra={"dsn_host": _dsn_host(self._dsn)},
            )
            self._pool = pool
            return pool

    async def save_turn(
        self,
        agent: str,
        request: AgentRequest,
        response: AgentResponse,
    ) -> int:
        """Persist a single turn; returns the new row id."""
        import asyncpg  # noqa: PLC0415

        tenant = get_tenant()
        try:
            # Inside the ``try``: pool creation is an I/O boundary like any
            # other. With it outside, connection-refused, bad-credentials,
            # wrong-database and connect-timeout escaped the typed vocabulary
            # and surfaced raw — including into the *unauthenticated* /readyz
            # body, which publishes ``str(exc)`` of whatever the repo raises.
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                row_id = await conn.fetchval(
                    "INSERT INTO turns (ts, agent, request, response, tenant) "
                    "VALUES ($1, $2, $3, $4, $5) RETURNING id",
                    datetime.now(UTC),
                    agent,
                    # model_dump(mode="json"), not model_dump_json(): the jsonb
                    # codec registered in _ensure_pool applies json.dumps on the
                    # way out, so handing it an already-serialised string stored
                    # a JSON *scalar* and read back a str — while SQLiteRepository
                    # returns a dict. That broke the row-shape parity the codec
                    # comment claims, and no test covered the shape.
                    request.model_dump(mode="json"),
                    response.model_dump(mode="json"),
                    tenant,
                )
        except _driver_failures(asyncpg) as exc:
            # ``logger.error``, not ``logger.exception``: the traceback renders
            # ``str(exc)``, and now that connection failures reach this handler
            # that body can be ``password authentication failed for user "…"``.
            # Same rule as ``_terminate_quietly`` — the type name only.
            logger.error(
                "Postgres save_turn failed",
                extra={"error": type(exc).__name__, "dsn_host": _dsn_host(self._dsn)},
            )
            raise PersistenceError(
                "Failed to persist turn",
                detail=f"{type(exc).__name__}: {exc}"[:DEFAULT_ERROR_DETAIL_TRUNCATE],
            ) from exc
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

    async def list_turns(
        self, limit: int = DEFAULT_STORAGE_LIST_TURNS_LIMIT
    ) -> list[dict[str, Any]]:
        """Return the most recent turns for the active tenant, newest first."""
        import asyncpg  # noqa: PLC0415

        tenant = get_tenant()
        try:
            # Inside the ``try`` for the same reason as ``save_turn`` — and it
            # matters most here: /readyz probes the DB through ``list_turns``.
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, ts, agent, request, response FROM turns "
                    "WHERE tenant = $1 ORDER BY id DESC LIMIT $2",
                    tenant,
                    limit,
                )
        except _driver_failures(asyncpg) as exc:
            # Type name only — see the note in ``save_turn``.
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

    def _terminate_quietly(self, pool: asyncpg.Pool) -> None:
        """Terminate *pool* immediately, absorbing a secondary failure.

        Shared by :meth:`close` and the ``_ensure_pool`` failure path, which
        must release the sockets of a pool it is about to discard.
        ``terminate()`` rather than ``close()``: the latter waits for every
        connection to be released, which would hang a bootstrap that is
        already failing (or being cancelled).

        Logs only the exception *type* — never the body. asyncpg exceptions
        can in some failure modes embed connection-URL text in the message;
        ``dsn_host`` is the only DSN-derived field we ever emit.
        """
        try:
            pool.terminate()
        except Exception as exc:  # pragma: no cover  -- defensive
            logger.warning(
                "Postgres pool terminate raised",
                extra={"error": type(exc).__name__, "dsn_host": _dsn_host(self._dsn)},
            )

    async def aclose(self) -> None:
        """Close the asyncpg pool cleanly (idempotent)."""
        if self._pool is None:
            return
        await self._pool.close()
        self._pool = None
        logger.info("asyncpg pool closed", extra={"dsn_host": _dsn_host(self._dsn)})

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
        self._terminate_quietly(self._pool)
        self._pool = None
        logger.info("asyncpg pool closed", extra={"dsn_host": _dsn_host(self._dsn)})
