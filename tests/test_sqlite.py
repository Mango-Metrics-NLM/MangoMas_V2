"""Tests for the SQLite repository."""

from __future__ import annotations

from pathlib import Path

import pytest

from mangomas.adapters.storage.sqlite import SQLiteRepository, _path_from_url
from mangomas.core import AgentRequest, AgentResponse, Message
from tests.constants import DEFAULT_AGENT_NAME


def test_path_from_url_variants(tmp_path: Path) -> None:
    assert _path_from_url(":memory:") == ":memory:"
    assert _path_from_url("file::memory:?cache=shared") == ":memory:"
    assert _path_from_url("sqlite:///abs/path.db") == "abs/path.db"
    assert _path_from_url("sqlite://relative.db") == "relative.db"
    assert _path_from_url(str(tmp_path / "x.db")) == str(tmp_path / "x.db")
    with pytest.raises(ValueError, match="Empty sqlite URL"):
        _path_from_url("sqlite://")


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
