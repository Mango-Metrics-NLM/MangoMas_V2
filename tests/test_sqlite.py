"""Tests for the SQLite repository."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mangomas.adapters.storage._url import path_from_sqlite_url
from mangomas.adapters.storage.sqlite import SQLiteRepository, _path_from_url
from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE
from mangomas.core import AgentRequest, AgentResponse, Message
from mangomas.errors import PersistenceError
from tests.constants import DEFAULT_AGENT_NAME, LARGE_UPSTREAM_BODY_CHARS


def test_path_from_url_variants(tmp_path: Path) -> None:
    assert _path_from_url(":memory:") == ":memory:"
    assert _path_from_url("file::memory:?cache=shared") == ":memory:"
    assert _path_from_url("sqlite:///abs/path.db") == "abs/path.db"
    assert _path_from_url("sqlite://relative.db") == "relative.db"
    assert _path_from_url(str(tmp_path / "x.db")) == str(tmp_path / "x.db")
    with pytest.raises(ValueError, match="Empty sqlite URL"):
        _path_from_url("sqlite://")


def test_path_from_url_is_the_shared_helper() -> None:
    """D4: ``sqlite.py``'s ``_path_from_url`` is a re-export, not a second
    implementation — proves the eval sqlite_results sink (which imports
    ``path_from_sqlite_url`` directly) shares behaviour with the repository.
    """
    assert _path_from_url is path_from_sqlite_url


async def test_save_and_list(repo: SQLiteRepository) -> None:
    req = AgentRequest(messages=[Message(role="user", content="hi")])
    resp = AgentResponse(content="ok", agent=DEFAULT_AGENT_NAME)
    rid = await repo.save_turn(DEFAULT_AGENT_NAME, req, resp)
    assert rid >= 1

    rows = await repo.list_turns()
    assert len(rows) == 1
    row = rows[0]
    assert row["agent"] == DEFAULT_AGENT_NAME
    request: dict[str, list[dict[str, str]]] = row["request"]
    response: dict[str, str] = row["response"]
    assert request["messages"][0]["content"] == "hi"
    assert response["content"] == "ok"


class _ExplodingConnection:
    """Stand-in for ``sqlite3.Connection`` whose every statement fails loudly.

    Raises an ``sqlite3.OperationalError`` carrying an oversized message so the
    truncation of client-visible ``PersistenceError.detail`` is observable.
    """

    def __init__(self, message: str) -> None:
        self._message = message

    def execute(self, *_args: object, **_kwargs: object) -> object:
        raise sqlite3.OperationalError(self._message)

    def close(self) -> None:  # pragma: no cover - defensive teardown surface
        return None


@pytest.mark.parametrize("method", ["save_turn", "list_turns"])
async def test_persistence_error_detail_is_truncated(method: str) -> None:
    """Regression (spec 0014 / D2): a ~5KB driver error must never reach the
    client-visible exception message; the truncated text lives in ``detail``,
    bounded by ``DEFAULT_ERROR_DETAIL_TRUNCATE``."""
    repo = SQLiteRepository(":memory:")
    repo._conn.close()  # release the real connection before swapping it out
    oversized = "x" * LARGE_UPSTREAM_BODY_CHARS
    repo._conn = _ExplodingConnection(oversized)  # type: ignore[assignment]

    with pytest.raises(PersistenceError) as excinfo:
        if method == "save_turn":
            req = AgentRequest(messages=[Message(role="user", content="hi")])
            resp = AgentResponse(content="ok", agent=DEFAULT_AGENT_NAME)
            await repo.save_turn(DEFAULT_AGENT_NAME, req, resp)
        else:
            await repo.list_turns()

    assert "x" * DEFAULT_ERROR_DETAIL_TRUNCATE not in str(excinfo.value)
    assert len(str(excinfo.value)) < DEFAULT_ERROR_DETAIL_TRUNCATE
    assert len(excinfo.value.detail) <= DEFAULT_ERROR_DETAIL_TRUNCATE
    assert excinfo.value.detail.startswith("OperationalError: x")


async def test_file_backed(tmp_path: Path) -> None:
    db = tmp_path / "nested" / "m.db"
    r = SQLiteRepository(f"sqlite:///{db}")
    try:
        req = AgentRequest(messages=[Message(role="user", content="x")])
        resp = AgentResponse(content="y", agent=DEFAULT_AGENT_NAME)
        await r.save_turn(DEFAULT_AGENT_NAME, req, resp)
        rows = await r.list_turns(limit=5)
        assert len(rows) == 1
    finally:
        r.close()
    assert db.exists()
