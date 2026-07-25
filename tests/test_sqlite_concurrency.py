"""Concurrency regression test for :class:`SQLiteRepository`.

The v0.1.0 changelog records a fix that wraps ``sqlite3`` access with a
``threading.Lock`` so concurrent writes from ``Orchestrator.dispatch_fan_out``
do not collide on the single shared connection. That fix was never
guarded by a direct regression test — the existing fan-out tests in
``tests/test_topologies.py`` use ``FakeRepository``.

This module exercises the lock for real: 50 concurrent ``save_turn``
calls against an in-memory SQLite repo. If the lock regresses (or someone
removes it), pytest will surface either an ``sqlite3.OperationalError``
(database is locked) or a violated unique-id assertion.
"""

from __future__ import annotations

import asyncio

from mangomas.adapters.storage import SQLiteRepository
from mangomas.core.agent import AgentRequest, AgentResponse, Message

CONCURRENT_WRITES: int = 50


async def test_save_turn_under_fan_out_yields_unique_ids(
    repo: SQLiteRepository,
) -> None:
    """50 parallel ``save_turn`` calls must all persist with unique row ids."""
    req = AgentRequest(messages=[Message(role="user", content="hi")])
    resp = AgentResponse(content="ok", agent="chat")

    ids = await asyncio.gather(
        *(repo.save_turn("chat", req, resp) for _ in range(CONCURRENT_WRITES))
    )

    assert len(ids) == CONCURRENT_WRITES
    assert len(set(ids)) == CONCURRENT_WRITES, "duplicate ids => lock regressed"
    assert all(rid >= 1 for rid in ids)

    rows = await repo.list_turns(limit=CONCURRENT_WRITES)
    assert len(rows) == CONCURRENT_WRITES
