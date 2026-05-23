"""Postgres persistence + ordering semantics."""

from __future__ import annotations

import pytest

from mangomas.adapters.storage.postgres import PostgresRepository
from mangomas.core.agent import AgentRequest, AgentResponse, Message

pytestmark = pytest.mark.postgres


async def test_list_turns_orders_newest_first(postgres_repo: PostgresRepository) -> None:
    req = AgentRequest(messages=[Message(role="user", content="m")])
    ids: list[int] = []
    for i in range(5):
        ids.append(
            await postgres_repo.save_turn(
                "chat",
                req,
                AgentResponse(content=f"reply-{i}", agent="chat"),
            )
        )

    rows = await postgres_repo.list_turns(limit=5)
    returned_ids = [row["id"] for row in rows]
    assert returned_ids == sorted(returned_ids, reverse=True)
    # All five we just inserted should be at the head of the list.
    assert set(returned_ids).issuperset(set(ids[-5:]))


async def test_list_turns_honours_limit(postgres_repo: PostgresRepository) -> None:
    req = AgentRequest(messages=[Message(role="user", content="m")])
    for i in range(3):
        await postgres_repo.save_turn("chat", req, AgentResponse(content=str(i), agent="chat"))

    rows = await postgres_repo.list_turns(limit=2)
    assert len(rows) == 2


async def test_schema_is_idempotent(postgres_repo: PostgresRepository) -> None:
    """A second ``_ensure_pool`` call must not raise (CREATE TABLE IF NOT EXISTS)."""
    pool = await postgres_repo._ensure_pool()
    same_pool = await postgres_repo._ensure_pool()
    assert pool is same_pool
