"""Postgres tenant isolation (spec 0007 / ADR-0017). Gated by RUN_POSTGRES=1."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from mangomas.adapters.storage.postgres import PostgresRepository
from mangomas.core.agent import AgentRequest, AgentResponse, Message
from mangomas.tenancy import set_tenant, tenant_id
from tests.constants import TENANT_A, TENANT_B

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def _reset_tenant() -> Iterator[None]:
    tenant_id.set(None)
    yield
    tenant_id.set(None)


def _req() -> AgentRequest:
    return AgentRequest(messages=[Message(role="user", content="hi")])


def _resp() -> AgentResponse:
    return AgentResponse(content="ok", agent="chat")


async def test_postgres_tenant_isolation(postgres_repo: PostgresRepository) -> None:
    set_tenant(TENANT_A)
    await postgres_repo.save_turn("chat", _req(), _resp())
    set_tenant(TENANT_B)
    await postgres_repo.save_turn("chat", _req(), _resp())
    await postgres_repo.save_turn("chat", _req(), _resp())

    b_turns = await postgres_repo.list_turns()
    set_tenant(TENANT_A)
    a_turns = await postgres_repo.list_turns()

    assert len(a_turns) == 1
    assert len(b_turns) == 2
