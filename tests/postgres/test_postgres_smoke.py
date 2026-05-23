"""Smoke tests for the live Postgres adapter.

Gated by ``RUN_POSTGRES=1`` and the ``postgres`` path component.
"""

from __future__ import annotations

import pytest

from mangomas.adapters.storage.postgres import PostgresRepository
from mangomas.core.agent import AgentRequest, AgentResponse, Message

pytestmark = pytest.mark.postgres


async def test_save_and_list_roundtrip(postgres_repo: PostgresRepository) -> None:
    req = AgentRequest(messages=[Message(role="user", content="hi")])
    resp = AgentResponse(content="ok", agent="chat")

    row_id = await postgres_repo.save_turn("chat", req, resp)
    assert row_id >= 1

    rows = await postgres_repo.list_turns(limit=10)
    assert len(rows) >= 1
    latest = rows[0]
    assert latest["agent"] == "chat"
    # JSONB codec gives us dicts, matching SQLite's json.loads output.
    assert isinstance(latest["request"], dict)
    assert isinstance(latest["response"], dict)
    assert latest["request"]["messages"][0]["content"] == "hi"
    assert latest["response"]["content"] == "ok"


async def test_aclose_is_idempotent(postgres_repo: PostgresRepository) -> None:
    await postgres_repo.aclose()
    await postgres_repo.aclose()  # second call must not raise
