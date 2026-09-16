"""Unit tests for :class:`PostgresRepository` — no live DB needed.

Covers DSN normalisation, host extraction for structured logging, and the
lazy-pool invariant that ``__init__`` performs no I/O.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from mangomas.adapters.storage import postgres as postgres_module
from mangomas.adapters.storage._schema import (
    TURN_SCHEMA_VERSION,
    TURN_SELECT_COLUMNS,
    TurnStatus,
)
from mangomas.adapters.storage.postgres import (
    _PG_RECORD_MIGRATIONS,
    _PG_SCHEMA,
    _PG_TENANT_MIGRATION,
    PostgresRepository,
    _driver_failures,
    _dsn_host,
    _normalise_dsn,
)
from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE, DBSettings
from mangomas.core.agent import AgentRequest, AgentResponse, Message
from mangomas.errors import PersistenceError
from mangomas.tenancy import DEFAULT_TENANT, get_tenant, set_tenant, tenant_id
from tests.constants import TENANT_A

#: Logger name the adapter emits under — derived, never restated, so a module
#: move cannot leave these assertions silently matching nothing.
_PG_LOGGER = postgres_module.__name__

# ── _normalise_dsn ────────────────────────────────────────────────────────────


def test_normalise_dsn_rewrites_postgres_scheme() -> None:
    assert _normalise_dsn("postgres://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"


def test_normalise_dsn_passes_canonical_form_through() -> None:
    assert _normalise_dsn("postgresql://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"


def test_normalise_dsn_strips_surrounding_whitespace() -> None:
    assert _normalise_dsn("  postgresql://h/db  ") == "postgresql://h/db"


@pytest.mark.parametrize("bad", ["", "   ", "\n\t"])
def test_normalise_dsn_rejects_empty_input(bad: str) -> None:
    with pytest.raises(ValueError, match="Empty Postgres DSN"):
        _normalise_dsn(bad)


# ── _dsn_host ─────────────────────────────────────────────────────────────────


def test_dsn_host_extracts_hostname() -> None:
    assert _dsn_host("postgresql://user:secret@db.example.test:5432/app") == "db.example.test"


def test_dsn_host_returns_none_for_unparseable() -> None:
    # An empty string parses but has no hostname.
    assert _dsn_host("") is None


def test_dsn_host_never_returns_password() -> None:
    host = _dsn_host("postgresql://user:secret-value@h/db")
    assert host == "h"
    # Sanity check: the password text never appears in the returned host.
    assert "secret" not in (host or "")


# ── PostgresRepository constructor ────────────────────────────────────────────


def test_constructor_does_no_io() -> None:
    """__init__ must not open the pool — required for the sync factory shape."""
    cfg = DBSettings(
        provider="postgres",
        url="postgresql://user:pw@nonexistent.invalid:5432/db",
        pool_min=2,
        pool_max=8,
        connect_timeout_seconds=1.0,
    )
    repo = PostgresRepository(cfg)
    assert repo._pool is None
    assert repo._dsn == "postgresql://user:pw@nonexistent.invalid:5432/db"
    assert repo._pool_min == 2
    assert repo._pool_max == 8
    assert repo._connect_timeout == 1.0


def test_constructor_normalises_legacy_postgres_scheme() -> None:
    cfg = DBSettings(provider="postgres", url="postgres://h/db")
    repo = PostgresRepository(cfg)
    assert repo._dsn == "postgresql://h/db"


def test_close_on_unused_repo_is_noop() -> None:
    """Sync close() on a never-opened pool must not raise."""
    cfg = DBSettings(provider="postgres", url="postgresql://h/db")
    repo = PostgresRepository(cfg)
    repo.close()  # no-op
    assert repo._pool is None


async def test_aclose_on_unused_repo_is_noop() -> None:
    """Async aclose() on a never-opened pool must not raise."""
    cfg = DBSettings(provider="postgres", url="postgresql://h/db")
    repo = PostgresRepository(cfg)
    await repo.aclose()  # no-op
    assert repo._pool is None


async def test_aclose_is_idempotent_without_pool() -> None:
    """Two back-to-back aclose() calls on a never-opened repo must not raise."""
    cfg = DBSettings(provider="postgres", url="postgresql://h/db")
    repo = PostgresRepository(cfg)
    await repo.aclose()
    await repo.aclose()
    assert repo._pool is None


def test_normalise_dsn_passes_driver_prefix_through() -> None:
    """asyncpg-driver suffixes like ``postgresql+asyncpg://`` aren't rewritten.

    The current adapter only normalises the legacy ``postgres://`` scheme. A
    SQLAlchemy-style driver prefix is preserved verbatim — operators using
    asyncpg directly never pass this form, but if they do (e.g. lifted from
    a SQLAlchemy URL) we don't silently mangle it.
    """
    dsn = "postgresql+asyncpg://u:p@h/db"
    assert _normalise_dsn(dsn) == dsn


# ── Helpers for mocking asyncpg pool / connections ────────────────────────────
# The methods under test (save_turn, list_turns) do `import asyncpg` in their
# body, so tests calling those methods require asyncpg to be installed.
# We use a try/except rather than pytest.importorskip to avoid skipping the
# DSN/constructor tests above that do NOT need asyncpg.
try:
    import asyncpg as _asyncpg

    _skip_no_asyncpg = False
except ImportError:
    _skip_no_asyncpg = True

_requires_asyncpg = pytest.mark.skipif(_skip_no_asyncpg, reason="asyncpg not installed")


def _make_cfg() -> DBSettings:
    """Return a minimal ``DBSettings`` for test ``PostgresRepository`` instances."""
    return DBSettings(provider="postgres", url="postgresql://u:p@testhost/db")


def _make_request() -> AgentRequest:
    return AgentRequest(messages=[Message(role="user", content="hi")])


def _make_response() -> AgentResponse:
    return AgentResponse(content="hello", agent="test-agent")


@dataclass
class FakeConnection:
    """Stub that records ``fetchval`` / ``fetch`` / ``execute`` calls."""

    fetchval_return: Any = 42
    fetch_return: list[dict[str, Any]] = field(default_factory=list)
    fetchval_side_effect: Exception | None = None
    fetch_side_effect: Exception | None = None
    execute_side_effect: Exception | None = None
    #: Parameters bound by the most recent ``fetchval`` call, so a test can
    #: assert what was actually handed to the jsonb codec.
    fetchval_args: tuple[Any, ...] = ()
    #: Every statement passed to ``execute``, in order — the DDL a real
    #: ``_ensure_pool`` runs against a borrowed connection.
    executed: list[str] = field(default_factory=list)
    #: Type codecs registered by the pool's ``init`` hook.
    codecs: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def fetchval(self, _query: str, *args: Any) -> Any:
        self.fetchval_args = args
        if self.fetchval_side_effect is not None:
            raise self.fetchval_side_effect
        return self.fetchval_return

    async def fetch(self, _query: str, *_args: Any) -> list[dict[str, Any]]:
        if self.fetch_side_effect is not None:
            raise self.fetch_side_effect
        return self.fetch_return

    async def execute(self, query: str, *_args: Any) -> str:
        self.executed.append(query)
        if self.execute_side_effect is not None:
            raise self.execute_side_effect
        return "OK"

    async def set_type_codec(self, name: str, **kwargs: Any) -> None:
        self.codecs[name] = kwargs


@dataclass
class FakePool:
    """Stub pool whose ``acquire()`` returns a ``FakeConnection`` context."""

    conn: FakeConnection = field(default_factory=FakeConnection)
    close_called: bool = False
    terminate_called: bool = False

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[FakeConnection]:
        yield self.conn

    async def close(self) -> None:
        self.close_called = True

    def terminate(self) -> None:
        self.terminate_called = True


@dataclass
class RecordingCreatePool:
    """Stub for ``asyncpg.create_pool`` — the seam the real ``_ensure_pool`` uses.

    Substituted for ``asyncpg.create_pool`` (a third-party callable, not an
    internal Protocol) so the production ``_ensure_pool`` body actually
    executes without a live database. It records every keyword it was handed,
    drives the ``init=`` hook exactly as asyncpg does, and can be told to fail.
    """

    pool: FakePool = field(default_factory=FakePool)
    side_effect: BaseException | None = None
    #: One dict of call kwargs per invocation, ``dsn`` included.
    calls: list[dict[str, Any]] = field(default_factory=list)
    #: Connection handed to the ``init=`` hook (asyncpg runs it per connection).
    init_conn: FakeConnection = field(default_factory=FakeConnection)

    async def __call__(self, dsn: str, **kwargs: Any) -> FakePool:
        self.calls.append({"dsn": dsn, **kwargs})
        if self.side_effect is not None:
            raise self.side_effect
        init = kwargs.get("init")
        if init is not None:
            await init(self.init_conn)
        return self.pool


def _patch_create_pool(
    monkeypatch: pytest.MonkeyPatch, creator: RecordingCreatePool
) -> RecordingCreatePool:
    """Swap ``asyncpg.create_pool`` for *creator* for the duration of a test."""
    monkeypatch.setattr(_asyncpg, "create_pool", creator)
    return creator


def _inject_pool(repo: PostgresRepository, pool: FakePool) -> None:
    """Bypass ``_ensure_pool`` by setting the internal pool directly."""
    repo._pool = pool  # FakePool duck-types asyncpg.Pool

    async def _noop_ensure() -> FakePool:
        return pool

    repo._ensure_pool = _noop_ensure  # type: ignore[method-assign]


# ── save_failed_turn (ADR-0031) ───────────────────────────────────────────────


@_requires_asyncpg
async def test_save_failed_turn_happy_path() -> None:
    """A failed dispatch persists on Postgres too, returning the row id.

    Parity with SQLite is the contract (ADR-0031): a deployment that swaps
    backends must not change what its audit trail can answer. This backend
    shipped the method for parity and nothing exercised it — the "parity
    claimed but not proven" shape the governance audit itself flags.
    """
    repo = PostgresRepository(_make_cfg())
    pool = FakePool(conn=FakeConnection(fetchval_return=11))
    _inject_pool(repo, pool)

    row_id = await repo.save_failed_turn(
        "bot", _make_request(), error_code="llm_timeout", error="upstream slow"
    )

    assert row_id == 11
    assert isinstance(row_id, int)


@_requires_asyncpg
async def test_save_failed_turn_binds_the_error_status_and_an_empty_response() -> None:
    """The row says *error*, names the code, and carries no response.

    The status column, not a sentinel inside the payload, is what distinguishes
    a failure — and ``response`` is an empty object rather than NULL because the
    column is NOT NULL on every existing database.
    """
    repo = PostgresRepository(_make_cfg())
    pool = FakePool(conn=FakeConnection(fetchval_return=1))
    _inject_pool(repo, pool)

    await repo.save_failed_turn("bot", _make_request(), error_code="step_timeout", error="too slow")

    # Positional order: ts, agent, request, response, tenant, schema_version,
    # status, error_code, error.
    args = pool.conn.fetchval_args
    assert args[3] == {}
    assert args[5] == TURN_SCHEMA_VERSION
    assert args[6] == str(TurnStatus.ERROR)
    assert args[7] == "step_timeout"
    assert args[8] == "too slow"


@_requires_asyncpg
async def test_save_failed_turn_truncates_a_runaway_error() -> None:
    """A huge driver message must not be written to the record verbatim.

    Same bound ``save_turn``'s own error detail uses. Without it a single
    pathological traceback could dominate the table this record exists to keep
    readable.
    """
    repo = PostgresRepository(_make_cfg())
    pool = FakePool(conn=FakeConnection(fetchval_return=1))
    _inject_pool(repo, pool)

    await repo.save_failed_turn("bot", _make_request(), error_code="llm_error", error="x" * 10_000)

    assert len(pool.conn.fetchval_args[8]) == DEFAULT_ERROR_DETAIL_TRUNCATE


@_requires_asyncpg
async def test_save_failed_turn_translates_postgres_error() -> None:
    """A driver failure while recording a failure is still a PersistenceError.

    The double-fault path: the repository itself breaking while writing the
    failure row. It must raise the typed error rather than leaking the driver's
    exception — ``_FailureRecordingMixin`` then swallows it so the caller keeps
    the original error, which is the one the operator needs.
    """
    pg_err = _asyncpg.PostgresError("boom")
    repo = PostgresRepository(_make_cfg())
    pool = FakePool(conn=FakeConnection(fetchval_side_effect=pg_err))
    _inject_pool(repo, pool)

    with pytest.raises(PersistenceError, match="Failed to persist turn") as exc_info:
        await repo.save_failed_turn(
            "bot", _make_request(), error_code="llm_timeout", error="upstream slow"
        )

    assert exc_info.value.__cause__ is pg_err


# ── save_turn ─────────────────────────────────────────────────────────────────


@_requires_asyncpg
async def test_save_turn_happy_path() -> None:
    """save_turn returns the integer row id on success."""
    repo = PostgresRepository(_make_cfg())
    pool = FakePool(conn=FakeConnection(fetchval_return=7))
    _inject_pool(repo, pool)

    row_id = await repo.save_turn("bot", _make_request(), _make_response())
    assert row_id == 7
    assert isinstance(row_id, int)


@_requires_asyncpg
async def test_save_turn_translates_postgres_error() -> None:
    """asyncpg.PostgresError is caught and re-raised as PersistenceError."""
    pg_err = _asyncpg.PostgresError("boom")

    repo = PostgresRepository(_make_cfg())
    pool = FakePool(conn=FakeConnection(fetchval_side_effect=pg_err))
    _inject_pool(repo, pool)

    with pytest.raises(PersistenceError, match="Failed to persist turn") as exc_info:
        await repo.save_turn("bot", _make_request(), _make_response())

    assert exc_info.value.__cause__ is pg_err
    assert "PostgresError" in exc_info.value.detail


# ── list_turns ────────────────────────────────────────────────────────────────


@_requires_asyncpg
def _fake_row(**overrides: Any) -> dict[str, Any]:
    """Build a fake asyncpg row carrying every column the real SELECT returns.

    Driven from ``TURN_SELECT_COLUMNS`` so a column added to the record shows up
    here automatically. A hand-written literal is what let these fixtures fall
    behind the schema in the first place — and the mapper is deliberately
    strict about missing keys, because tolerating them is how a backend
    silently stops returning a column.
    """
    row: dict[str, Any] = dict.fromkeys(TURN_SELECT_COLUMNS)
    row.update(
        {
            "id": 1,
            "ts": datetime.now(UTC),
            "agent": "bot",
            "request": {},
            "response": {},
            "schema_version": TURN_SCHEMA_VERSION,
            "status": str(TurnStatus.OK),
        }
    )
    row.update(overrides)
    return row


async def test_list_turns_happy_path() -> None:
    """list_turns returns dicts with the expected keys."""
    ts = datetime.now(UTC)
    fake_rows = [
        _fake_row(
            ts=ts,
            request={"messages": [{"role": "user", "content": "hi"}]},
            response={"content": "hello", "agent": "bot"},
        )
    ]
    repo = PostgresRepository(_make_cfg())
    pool = FakePool(conn=FakeConnection(fetch_return=fake_rows))
    _inject_pool(repo, pool)

    result = await repo.list_turns(limit=10)
    assert len(result) == 1
    row = result[0]
    assert row["id"] == 1
    assert row["ts"] == ts.isoformat()
    assert row["agent"] == "bot"
    assert row["request"] == fake_rows[0]["request"]
    assert row["response"] == fake_rows[0]["response"]


@_requires_asyncpg
async def test_list_turns_empty_result() -> None:
    """list_turns returns an empty list when there are no turns."""
    repo = PostgresRepository(_make_cfg())
    pool = FakePool(conn=FakeConnection(fetch_return=[]))
    _inject_pool(repo, pool)

    result = await repo.list_turns()
    assert result == []


@_requires_asyncpg
async def test_list_turns_handles_none_ts() -> None:
    """list_turns maps a NULL ts to Python None."""
    fake_rows: list[dict[str, Any]] = [_fake_row(id=2, ts=None)]
    repo = PostgresRepository(_make_cfg())
    pool = FakePool(conn=FakeConnection(fetch_return=fake_rows))
    _inject_pool(repo, pool)

    result = await repo.list_turns()
    assert result[0]["ts"] is None


@_requires_asyncpg
async def test_list_turns_translates_postgres_error() -> None:
    """asyncpg.PostgresError is caught and re-raised as PersistenceError."""
    pg_err = _asyncpg.PostgresError("select boom")

    repo = PostgresRepository(_make_cfg())
    pool = FakePool(conn=FakeConnection(fetch_side_effect=pg_err))
    _inject_pool(repo, pool)

    with pytest.raises(PersistenceError, match="Failed to list turns") as exc_info:
        await repo.list_turns()

    assert exc_info.value.__cause__ is pg_err


# ── aclose with live pool ────────────────────────────────────────────────────


async def test_aclose_with_pool_calls_close_and_clears() -> None:
    """aclose() should await pool.close() then set _pool to None."""
    repo = PostgresRepository(_make_cfg())
    pool = FakePool()
    repo._pool = pool  # FakePool duck-types asyncpg.Pool

    await repo.aclose()
    assert pool.close_called is True
    assert repo._pool is None


async def test_aclose_idempotent_after_close() -> None:
    """Calling aclose() twice on a repo with a pool must not raise."""
    repo = PostgresRepository(_make_cfg())
    pool = FakePool()
    repo._pool = pool  # FakePool duck-types asyncpg.Pool

    await repo.aclose()
    await repo.aclose()  # second call — pool is None, should no-op
    assert repo._pool is None


# ── close (sync) with live pool ───────────────────────────────────────────────


def test_close_with_pool_calls_terminate_and_clears() -> None:
    """close() should call pool.terminate() then set _pool to None."""
    repo = PostgresRepository(_make_cfg())
    pool = FakePool()
    repo._pool = pool  # FakePool duck-types asyncpg.Pool

    repo.close()
    assert pool.terminate_called is True
    assert repo._pool is None


def test_close_idempotent_after_terminate() -> None:
    """Calling close() twice on a repo with a pool must not raise."""
    repo = PostgresRepository(_make_cfg())
    pool = FakePool()
    repo._pool = pool  # FakePool duck-types asyncpg.Pool

    repo.close()
    repo.close()  # second call — pool is None, should no-op
    assert repo._pool is None


# ── jsonb row-shape parity with SQLite (regression) ───────────────────────────


async def test_save_turn_binds_json_objects_not_pre_serialised_strings() -> None:
    """``save_turn`` must bind dicts so the jsonb codec produces a JSON object.

    Regression. ``_ensure_pool`` registers ``encoder=json.dumps`` for jsonb with
    the comment "Decode jsonb columns into dicts to match SQLiteRepository's row
    shape". ``save_turn`` then bound ``model_dump_json()`` — already a string —
    so the encoder serialised it a *second* time. The column held a JSON scalar
    and ``list_turns`` returned ``str`` where SQLite returns ``dict``.

    Asserted here rather than in ``tests/postgres/`` because that suite is gated
    behind ``RUN_POSTGRES=1``, which CI never sets — the defect shipped with a
    green pipeline. This test needs no database: it checks the values bound, then
    round-trips them through the codec functions the pool actually registers.
    """
    pool = FakePool()
    repo = PostgresRepository(_make_cfg())
    _inject_pool(repo, pool)

    request, response = _make_request(), _make_response()
    await repo.save_turn("chat", request, response)

    # Positional order: ts, agent, request, response, tenant.
    bound_request, bound_response = pool.conn.fetchval_args[2], pool.conn.fetchval_args[3]
    assert isinstance(bound_request, dict), f"bound a {type(bound_request).__name__}, not a dict"
    assert isinstance(bound_response, dict)

    # The codec round-trip must yield the same dicts SQLiteRepository returns.
    for bound, model in ((bound_request, request), (bound_response, response)):
        assert json.loads(json.dumps(bound)) == model.model_dump(mode="json")


# ── _ensure_pool: the real body, with a stubbed ``create_pool`` ────────────────
# Everything below deliberately does NOT call ``_inject_pool``: that helper
# monkeypatches ``_ensure_pool`` away wholesale, which is why the entire pool
# bootstrap (creation, server settings, schema DDL, failure handling) was
# unexecuted by the unit suite while the file reported 80 % coverage.


@_requires_asyncpg
async def test_ensure_pool_creates_applies_ddl_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bootstrap runs both DDL statements and then caches the pool."""
    repo = PostgresRepository(_make_cfg())
    creator = _patch_create_pool(monkeypatch, RecordingCreatePool())

    pool = await repo._ensure_pool()

    assert pool is creator.pool
    assert repo._pool is creator.pool
    assert creator.pool.conn.executed == [
        _PG_SCHEMA,
        _PG_TENANT_MIGRATION,
        *_PG_RECORD_MIGRATIONS,
    ]

    # Second call must reuse the cached pool rather than rebuild it.
    assert await repo._ensure_pool() is creator.pool
    assert len(creator.calls) == 1


@_requires_asyncpg
async def test_ensure_pool_forwards_sizing_and_connect_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pool sizing + connect budget come from ``DBSettings``, never a literal."""
    cfg = DBSettings(
        provider="postgres",
        url="postgres://u:p@testhost/db",
        pool_min=3,
        pool_max=9,
        connect_timeout_seconds=4.5,
    )
    repo = PostgresRepository(cfg)
    creator = _patch_create_pool(monkeypatch, RecordingCreatePool())

    await repo._ensure_pool()

    call = creator.calls[0]
    # The legacy ``postgres://`` scheme is still rewritten before create_pool.
    assert call["dsn"] == "postgresql://u:p@testhost/db"
    assert call["min_size"] == 3
    assert call["max_size"] == 9
    assert call["timeout"] == 4.5


@_requires_asyncpg
async def test_ensure_pool_registers_jsonb_codec_via_init_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``init=`` hook runs per connection and registers the jsonb codec."""
    repo = PostgresRepository(_make_cfg())
    creator = _patch_create_pool(monkeypatch, RecordingCreatePool())

    await repo._ensure_pool()

    codec = creator.init_conn.codecs["jsonb"]
    assert codec["schema"] == "pg_catalog"
    assert codec["encoder"] is json.dumps
    assert codec["decoder"] is json.loads


# ── DEFECT 1: MANGOMAS_DB__STATEMENT_TIMEOUT_SECONDS must bind the whole pool ──


@_requires_asyncpg
async def test_statement_timeout_is_pool_wide_not_one_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``statement_timeout`` must reach every pooled connection.

    Regression (defect 1). The adapter used to ``SET statement_timeout`` on the
    single connection it borrowed for DDL. That GUC is session-scoped, so the
    other ``pool_min..pool_max`` connections never received it — and asyncpg's
    ``Connection.reset()`` issues ``RESET ALL`` on release, discarding it even
    for that one. The documented knob did nothing.
    """
    cfg = DBSettings(
        provider="postgres",
        url="postgresql://u:p@testhost/db",
        pool_min=2,
        pool_max=8,
        statement_timeout_seconds=2.5,
    )
    repo = PostgresRepository(cfg)
    creator = _patch_create_pool(monkeypatch, RecordingCreatePool())

    await repo._ensure_pool()

    server_settings = creator.calls[0].get("server_settings")
    assert server_settings is not None, "statement_timeout never reached create_pool"
    # asyncpg sends server_settings in the startup packet, so the value applies
    # to every connection and survives RESET ALL. Unit is milliseconds.
    assert server_settings["statement_timeout"] == "2500"

    # ...and it must NOT be re-implemented as a per-session SET.
    assert not any("statement_timeout" in stmt for stmt in creator.pool.conn.executed)


@_requires_asyncpg
async def test_statement_timeout_unset_sends_no_server_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Off by default: an unset timeout must not invent a server setting."""
    repo = PostgresRepository(_make_cfg())
    creator = _patch_create_pool(monkeypatch, RecordingCreatePool())

    await repo._ensure_pool()

    assert creator.calls[0].get("server_settings") is None
    assert not any("statement_timeout" in stmt for stmt in creator.pool.conn.executed)


# ── DEFECT 2: a failed DDL must not latch a schema-less pool ──────────────────


@_requires_asyncpg
async def test_failed_ddl_does_not_latch_a_schemaless_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (defect 2): ``self._pool`` is published only after the DDL lands.

    It used to be assigned *before* the schema/migration statements ran, so a
    DDL failure left a pool whose table was never created cached on the
    instance. Every later call took the early return and failed on a missing
    relation until the process restarted.
    """
    boom = _asyncpg.PostgresError("permission denied for schema public")
    broken = FakePool(conn=FakeConnection(execute_side_effect=boom))
    creator = _patch_create_pool(monkeypatch, RecordingCreatePool(pool=broken))
    repo = PostgresRepository(_make_cfg())

    with pytest.raises(_asyncpg.PostgresError):
        await repo._ensure_pool()

    assert repo._pool is None, "a pool whose DDL failed stayed latched on the instance"
    assert broken.terminate_called is True, "the half-built pool was never released"
    assert len(creator.calls) == 1


@_requires_asyncpg
async def test_pool_is_retryable_after_a_failed_ddl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient DDL failure must leave the adapter retryable, not broken."""
    boom = _asyncpg.PostgresError("deadlock detected")
    creator = RecordingCreatePool(pool=FakePool(conn=FakeConnection(execute_side_effect=boom)))
    _patch_create_pool(monkeypatch, creator)
    repo = PostgresRepository(_make_cfg())

    with pytest.raises(_asyncpg.PostgresError):
        await repo._ensure_pool()

    # The cause clears; the next call must rebuild from scratch.
    healthy = FakePool()
    creator.pool = healthy

    assert await repo._ensure_pool() is healthy
    assert healthy.conn.executed == [_PG_SCHEMA, _PG_TENANT_MIGRATION, *_PG_RECORD_MIGRATIONS]
    assert len(creator.calls) == 2


@_requires_asyncpg
async def test_save_turn_recovers_on_the_call_after_a_failed_ddl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The operator-visible shape of defect 2: one bad boot must not be terminal."""
    boom = _asyncpg.PostgresError("could not create table")
    creator = RecordingCreatePool(pool=FakePool(conn=FakeConnection(execute_side_effect=boom)))
    _patch_create_pool(monkeypatch, creator)
    repo = PostgresRepository(_make_cfg())

    with pytest.raises(PersistenceError, match="Failed to persist turn"):
        await repo.save_turn("bot", _make_request(), _make_response())

    creator.pool = FakePool(conn=FakeConnection(fetchval_return=11))
    assert await repo.save_turn("bot", _make_request(), _make_response()) == 11


# ── DEFECT 3: every driver failure mode becomes a PersistenceError ─────────────

#: Representative failures asyncpg raises that are *not* ``PostgresError``
#: subclasses, plus the two that are but used to escape because
#: ``_ensure_pool()`` sat outside the ``try``.
_DRIVER_FAILURE_KINDS: list[str] = [
    "connection_refused",
    "dns_failure",
    "bad_credentials",
    "wrong_database",
    "connect_timeout",
    "pool_closing",
    "internal_client_error",
]


def _driver_failure(kind: str) -> Exception:
    """Build one representative driver failure.

    Constructed lazily inside a test rather than at import, so this module
    still imports when the optional ``postgres`` extra is absent.
    """
    builders: dict[str, Callable[[], Exception]] = {
        "connection_refused": lambda: ConnectionRefusedError(111, "Connection refused"),
        "dns_failure": lambda: OSError("[Errno -2] Name or service not known"),
        "bad_credentials": lambda: _asyncpg.InvalidPasswordError(
            'password authentication failed for user "app_user"'
        ),
        "wrong_database": lambda: _asyncpg.InvalidCatalogNameError(
            'database "nope" does not exist'
        ),
        "connect_timeout": lambda: TimeoutError("connect budget exhausted"),
        "pool_closing": lambda: _asyncpg.InterfaceError("pool is closing"),
        "internal_client_error": lambda: _asyncpg.InternalClientError("unexpected protocol state"),
    }
    return builders[kind]()


@_requires_asyncpg
def test_driver_failure_vocabulary_is_wider_than_postgres_error() -> None:
    """``asyncpg.PostgresError`` is only the server-reported half of the set."""
    failures = _driver_failures(_asyncpg)

    # Verified against the installed asyncpg: these are outside PostgresError.
    assert not issubclass(_asyncpg.InterfaceError, _asyncpg.PostgresError)
    assert not issubclass(_asyncpg.InternalClientError, _asyncpg.PostgresError)
    assert not issubclass(TimeoutError, _asyncpg.PostgresError)
    assert not issubclass(OSError, _asyncpg.PostgresError)

    for cls in (
        _asyncpg.PostgresError,
        _asyncpg.InterfaceError,
        _asyncpg.InternalClientError,
        OSError,
        TimeoutError,
    ):
        assert issubclass(cls, failures), f"{cls.__name__} escapes the typed vocabulary"

    # Cancellation must propagate — it is shutdown, not a persistence fault.
    assert not issubclass(asyncio.CancelledError, failures)


@_requires_asyncpg
@pytest.mark.parametrize("kind", _DRIVER_FAILURE_KINDS)
async def test_save_turn_wraps_pool_creation_failure(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Regression (defect 3): ``_ensure_pool()`` failures are persistence failures.

    ``pool = await self._ensure_pool()`` sat outside the ``try``, so
    connection-refused, bad-credentials, wrong-database and connect-timeout
    surfaced raw instead of as ``PersistenceError`` (500).
    """
    exc = _driver_failure(kind)
    _patch_create_pool(monkeypatch, RecordingCreatePool(side_effect=exc))
    repo = PostgresRepository(_make_cfg())

    with pytest.raises(PersistenceError, match="Failed to persist turn") as info:
        await repo.save_turn("bot", _make_request(), _make_response())

    assert info.value.__cause__ is exc
    assert type(exc).__name__ in info.value.detail


@_requires_asyncpg
@pytest.mark.parametrize("kind", _DRIVER_FAILURE_KINDS)
async def test_list_turns_wraps_pool_creation_failure(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Same for the read path — ``/readyz`` probes through ``list_turns``."""
    exc = _driver_failure(kind)
    _patch_create_pool(monkeypatch, RecordingCreatePool(side_effect=exc))
    repo = PostgresRepository(_make_cfg())

    with pytest.raises(PersistenceError, match="Failed to list turns") as info:
        await repo.list_turns()

    assert info.value.__cause__ is exc


@_requires_asyncpg
@pytest.mark.parametrize("kind", ["pool_closing", "connect_timeout", "internal_client_error"])
async def test_save_turn_wraps_non_postgres_error_during_query(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """The ``except`` must be wider than ``asyncpg.PostgresError``.

    ``InterfaceError`` (acquire on a closing pool), ``TimeoutError`` (acquire
    exhaustion) and ``InternalClientError`` are all outside that hierarchy.
    """
    exc = _driver_failure(kind)
    creator = RecordingCreatePool(pool=FakePool(conn=FakeConnection(fetchval_side_effect=exc)))
    _patch_create_pool(monkeypatch, creator)
    repo = PostgresRepository(_make_cfg())

    with pytest.raises(PersistenceError, match="Failed to persist turn") as info:
        await repo.save_turn("bot", _make_request(), _make_response())

    assert info.value.__cause__ is exc


@_requires_asyncpg
@pytest.mark.parametrize("kind", ["pool_closing", "connect_timeout", "internal_client_error"])
async def test_list_turns_wraps_non_postgres_error_during_query(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Read-path twin of the above."""
    exc = _driver_failure(kind)
    creator = RecordingCreatePool(pool=FakePool(conn=FakeConnection(fetch_side_effect=exc)))
    _patch_create_pool(monkeypatch, creator)
    repo = PostgresRepository(_make_cfg())

    with pytest.raises(PersistenceError, match="Failed to list turns") as info:
        await repo.list_turns()

    assert info.value.__cause__ is exc


@_requires_asyncpg
async def test_cancellation_is_not_swallowed_as_a_persistence_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Widening the ``except`` must not capture ``asyncio.CancelledError``."""
    _patch_create_pool(
        monkeypatch, RecordingCreatePool(side_effect=asyncio.CancelledError("shutdown"))
    )
    repo = PostgresRepository(_make_cfg())

    with pytest.raises(asyncio.CancelledError):
        await repo.save_turn("bot", _make_request(), _make_response())


@_requires_asyncpg
async def test_auth_failure_message_never_reaches_the_public_error_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/readyz`` is unauthenticated and publishes ``str(exc)``.

    ``check_ready`` copies ``str(exc)`` of whatever ``list_turns`` raises into
    the readiness body. An unwrapped ``InvalidPasswordError`` therefore
    published ``password authentication failed for user "..."`` to anonymous
    callers. Wrapping restores the fixed, driver-free message.
    """
    secret = 'password authentication failed for user "app_user"'  # noqa: S105  driver text
    _patch_create_pool(
        monkeypatch, RecordingCreatePool(side_effect=_asyncpg.InvalidPasswordError(secret))
    )
    repo = PostgresRepository(_make_cfg())

    with pytest.raises(PersistenceError) as info:
        await repo.list_turns()

    assert str(info.value) == "Failed to list turns"
    assert "password authentication failed" not in str(info.value)


# ── Tenancy stays correct through the rebuilt paths ──────────────────────────


@_requires_asyncpg
async def test_save_turn_binds_active_tenant_through_the_real_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tenant row filter must survive the ``_ensure_pool``-inside-try move."""
    creator = _patch_create_pool(monkeypatch, RecordingCreatePool())
    repo = PostgresRepository(_make_cfg())

    token = tenant_id.set(None)
    try:
        set_tenant(TENANT_A)
        await repo.save_turn("bot", _make_request(), _make_response())
        # Positional order: ts, agent, request, response, tenant.
        assert creator.pool.conn.fetchval_args[4] == TENANT_A
    finally:
        tenant_id.reset(token)


@_requires_asyncpg
async def test_list_turns_defaults_to_the_default_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no tenant set the read still filters — on ``DEFAULT_TENANT``."""
    _patch_create_pool(monkeypatch, RecordingCreatePool())
    repo = PostgresRepository(_make_cfg())

    token = tenant_id.set(None)
    try:
        assert await repo.list_turns() == []
    finally:
        tenant_id.reset(token)
    assert get_tenant() == DEFAULT_TENANT


# ── Pool-lifecycle logging: structured, and never credential-bearing ──────────

#: A DSN with a distinctive password, so a leak into any log record is
#: unambiguous rather than a substring coincidence.
_SECRET_DSN = "postgresql://app_user:sup3r-secret-pw@db.example.test:5432/appdb"  # noqa: S105  test-only
_DSN_PASSWORD = "sup3r-secret-pw"  # noqa: S105  test-only
_DSN_HOST = "db.example.test"


def _log_blob(caplog: pytest.LogCaptureFixture) -> str:
    """Formatted log text plus every structured ``extra`` field, as one string.

    Deliberately spans *every* logger, not just the adapter's: a credential
    leaking out through some other logger is still a leak.
    """
    extras = "".join(repr(record.__dict__) for record in caplog.records)
    return caplog.text + extras


def _adapter_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Only the adapter's own records.

    ``caplog.at_level`` raises a logger's level but captures at the root
    handler, so a sibling logger left at INFO by an earlier test would
    otherwise fail the ``dsn_host``-on-every-record assertion below.
    """
    return [record for record in caplog.records if record.name == _PG_LOGGER]


@_requires_asyncpg
async def test_pool_lifecycle_emits_structured_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """created / DDL applied / closed each get a record carrying ``dsn_host``."""
    repo = PostgresRepository(DBSettings(provider="postgres", url=_SECRET_DSN))
    _patch_create_pool(monkeypatch, RecordingCreatePool())

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger=_PG_LOGGER):
        await repo._ensure_pool()
        await repo.aclose()

    records = _adapter_records(caplog)
    messages = [record.getMessage() for record in records]
    assert any("pool created" in m for m in messages), messages
    assert any("schema" in m for m in messages), messages
    assert any("closed" in m for m in messages), messages
    # Every lifecycle line is structured and host-scoped — never DSN-bearing.
    assert all(getattr(record, "dsn_host", None) == _DSN_HOST for record in records), messages


@_requires_asyncpg
async def test_pool_lifecycle_logs_never_carry_dsn_or_credentials(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Only ``dsn_host`` is ever emitted — never the DSN or its password."""
    repo = PostgresRepository(DBSettings(provider="postgres", url=_SECRET_DSN))
    _patch_create_pool(monkeypatch, RecordingCreatePool())

    with caplog.at_level(logging.DEBUG, logger=_PG_LOGGER):
        await repo._ensure_pool()
        await repo.save_turn("bot", _make_request(), _make_response())
        repo.close()

    blob = _log_blob(caplog)
    assert _DSN_PASSWORD not in blob
    assert _SECRET_DSN not in blob


@_requires_asyncpg
@pytest.mark.parametrize("kind", ["bad_credentials", "connection_refused"])
async def test_failure_logs_record_only_the_exception_type(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, kind: str
) -> None:
    """A raw driver message must never reach a log record — type name only.

    ``logger.exception`` would render the traceback, and an asyncpg auth
    failure embeds ``password authentication failed for user "..."`` in its
    message. ``close()`` already models the right behaviour (it logs
    ``type(exc).__name__`` and says why); the query paths now match it.
    """
    exc = _driver_failure(kind)
    _patch_create_pool(monkeypatch, RecordingCreatePool(side_effect=exc))
    repo = PostgresRepository(DBSettings(provider="postgres", url=_SECRET_DSN))

    with (
        caplog.at_level(logging.DEBUG, logger=_PG_LOGGER),
        pytest.raises(PersistenceError),
    ):
        await repo.save_turn("bot", _make_request(), _make_response())

    blob = _log_blob(caplog)
    assert str(exc) not in blob, "the raw driver message reached the logs"
    assert _DSN_PASSWORD not in blob
    assert any(getattr(r, "error", None) == type(exc).__name__ for r in caplog.records)
