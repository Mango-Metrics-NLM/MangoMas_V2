"""Concurrency regression for :class:`PostgresRepository`.

Mirrors ``tests/test_sqlite_concurrency.py`` (which covers the SQLite
``threading.Lock`` invariant). The Postgres adapter has no lock — it
relies on asyncpg's connection pool — so this test pins the pool's
concurrent-write correctness.
"""

from __future__ import annotations

import asyncio

import pytest

from mangomas.adapters.storage.postgres import PostgresRepository
from mangomas.core.agent import AgentRequest, AgentResponse, Message

pytestmark = pytest.mark.postgres

CONCURRENT_WRITES: int = 50


async def test_save_turn_under_fan_out_yields_unique_ids(
    postgres_repo: PostgresRepository,
) -> None:
    """50 parallel ``save_turn`` calls must all persist with unique row ids."""
    req = AgentRequest(messages=[Message(role="user", content="hi")])
    resp = AgentResponse(content="ok", agent="chat")

    ids = await asyncio.gather(
        *(postgres_repo.save_turn("chat", req, resp) for _ in range(CONCURRENT_WRITES))
    )

    assert len(ids) == CONCURRENT_WRITES
    assert len(set(ids)) == CONCURRENT_WRITES, "duplicate ids => pool regressed"
    assert all(rid >= 1 for rid in ids)
