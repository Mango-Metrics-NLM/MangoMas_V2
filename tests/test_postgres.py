"""Unit tests for :class:`PostgresRepository` — no live DB needed.

Covers DSN normalisation, host extraction for structured logging, and the
lazy-pool invariant that ``__init__`` performs no I/O.
"""

from __future__ import annotations

import pytest

from mangomas.adapters.storage.postgres import (
    PostgresRepository,
    _dsn_host,
    _normalise_dsn,
)
from mangomas.config import DBSettings

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
