"""Unit tests for :class:`PostgresRepository` — no live DB needed.

Covers DSN normalisation, host extraction for structured logging, and the
lazy-pool invariant that ``__init__`` performs no I/O.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from mangomas.adapters.storage.postgres import (
    PostgresRepository,
    _dsn_host,
    _normalise_dsn,
)
from mangomas.config import DBSettings
from mangomas.core.agent import AgentRequest, AgentResponse, Message
from mangomas.errors import PersistenceError

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
    """Stub that records ``fetchval`` / ``fetch`` calls."""

    fetchval_return: Any = 42
    fetch_return: list[dict[str, Any]] = field(default_factory=list)
    fetchval_side_effect: Exception | None = None
    fetch_side_effect: Exception | None = None

    async def fetchval(self, _query: str, *_args: Any) -> Any:
        if self.fetchval_side_effect is not None:
            raise self.fetchval_side_effect
        return self.fetchval_return

    async def fetch(self, _query: str, *_args: Any) -> list[dict[str, Any]]:
        if self.fetch_side_effect is not None:
            raise self.fetch_side_effect
        return self.fetch_return


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


def _inject_pool(repo: PostgresRepository, pool: FakePool) -> None:
    """Bypass ``_ensure_pool`` by setting the internal pool directly."""
    repo._pool = pool  # FakePool duck-types asyncpg.Pool

    async def _noop_ensure() -> FakePool:
        return pool

    repo._ensure_pool = _noop_ensure  # type: ignore[method-assign]


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
async def test_list_turns_happy_path() -> None:
    """list_turns returns dicts with the expected keys."""
    ts = datetime.now(UTC)
    fake_rows = [
        {
            "id": 1,
            "ts": ts,
            "agent": "bot",
            "request": {"messages": [{"role": "user", "content": "hi"}]},
            "response": {"content": "hello", "agent": "bot"},
        }
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
    fake_rows: list[dict[str, Any]] = [
        {
            "id": 2,
            "ts": None,
            "agent": "bot",
            "request": {},
            "response": {},
        }
    ]
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
