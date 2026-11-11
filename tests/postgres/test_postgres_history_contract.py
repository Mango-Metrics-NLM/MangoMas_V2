"""Contract tests asserting turn & history JSON line parity between Postgres and SQLite.

Validates:
1. Exact column and field parity across `list_turns()` outputs.
2. Value and type parity for successful turns (`save_turn`).
3. Value and type parity for failed turns (`save_failed_turn`).
4. JSON line serialization parity (`json.dumps(row, ensure_ascii=False)`)
   matching CLI `mangomas history`.
5. Multi-tenancy isolation parity (`X-Tenant-ID` / ContextVar scoping).
6. Chronological ordering (newest first) and pagination limit parity.

Gated by `RUN_POSTGRES=1` via `tests/conftest.py`.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import datetime

import pytest

from mangomas.adapters.storage import SQLiteRepository
from mangomas.adapters.storage._schema import TURN_SCHEMA_VERSION, TURN_SELECT_COLUMNS, TurnStatus
from mangomas.adapters.storage.postgres import PostgresRepository
from mangomas.core.agent import AgentRequest, AgentResponse, Message
from mangomas.tenancy import get_tenant, set_tenant, tenant_id
from tests.constants import IN_MEMORY_SQLITE_URL

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def _isolate_contract_tenant() -> Iterator[str]:
    """Ensure each contract test runs in an isolated tenant partition."""
    test_tenant = f"contract-{uuid.uuid4()}"
    token = tenant_id.set(test_tenant)
    try:
        yield test_tenant
    finally:
        tenant_id.reset(token)


@pytest.fixture
def sqlite_repo() -> Iterator[SQLiteRepository]:
    """Yield an in-memory SQLiteRepository instance."""
    repo = SQLiteRepository(IN_MEMORY_SQLITE_URL)
    try:
        yield repo
    finally:
        repo.close()


def _make_request(prompt: str = "Test message") -> AgentRequest:
    return AgentRequest(messages=[Message(role="user", content=prompt)])


def _make_response(reply: str = "Test response", agent: str = "chat") -> AgentResponse:
    return AgentResponse(content=reply, agent=agent)


def _strip_dynamic_keys(record: dict[str, object]) -> dict[str, object]:
    """Strip auto-incrementing ID and wall-clock timestamp for static parity comparison."""
    return {k: v for k, v in record.items() if k not in ("id", "ts")}


async def test_history_schema_and_keys_parity(
    postgres_repo: PostgresRepository, sqlite_repo: SQLiteRepository
) -> None:
    """Both repositories must return the exact same column keys matching TURN_SELECT_COLUMNS."""
    req = _make_request("Verify keys")
    resp = _make_response("Keys verified")

    await sqlite_repo.save_turn("chat", req, resp)
    await postgres_repo.save_turn("chat", req, resp)

    sqlite_rows = await sqlite_repo.list_turns(limit=1)
    pg_rows = await postgres_repo.list_turns(limit=1)

    assert len(sqlite_rows) == 1
    assert len(pg_rows) == 1

    expected_keys = set(TURN_SELECT_COLUMNS)
    assert set(sqlite_rows[0].keys()) == expected_keys
    assert set(pg_rows[0].keys()) == expected_keys


async def test_successful_turn_roundtrip_field_parity(
    postgres_repo: PostgresRepository, sqlite_repo: SQLiteRepository
) -> None:
    """Field types and values must be interchangeable between backends on success."""
    req = _make_request("Calculate deployment metrics")
    resp = _make_response("Metrics: 99.9% uptime", agent="chat")

    sq_id = await sqlite_repo.save_turn("chat", req, resp)
    pg_id = await postgres_repo.save_turn("chat", req, resp)

    assert isinstance(sq_id, int) and sq_id >= 1
    assert isinstance(pg_id, int) and pg_id >= 1

    sq_rows = await sqlite_repo.list_turns(limit=1)
    pg_rows = await postgres_repo.list_turns(limit=1)

    sq_row = sq_rows[0]
    pg_row = pg_rows[0]

    # Types
    assert isinstance(sq_row["id"], int) and isinstance(pg_row["id"], int)
    assert isinstance(sq_row["ts"], str) and isinstance(pg_row["ts"], str)
    assert datetime.fromisoformat(sq_row["ts"])  # Valid ISO-8601
    assert datetime.fromisoformat(pg_row["ts"])  # Valid ISO-8601

    # Exact field parity
    assert sq_row["agent"] == pg_row["agent"] == "chat"
    assert sq_row["schema_version"] == pg_row["schema_version"] == TURN_SCHEMA_VERSION
    assert sq_row["status"] == pg_row["status"] == str(TurnStatus.OK)
    assert sq_row["error_code"] is None and pg_row["error_code"] is None
    assert sq_row["error"] is None and pg_row["error"] is None

    # JSON decoded shapes
    assert isinstance(sq_row["request"], dict) and isinstance(pg_row["request"], dict)
    assert isinstance(sq_row["response"], dict) and isinstance(pg_row["response"], dict)
    assert sq_row["request"] == pg_row["request"] == req.model_dump(mode="json")
    assert sq_row["response"] == pg_row["response"] == resp.model_dump(mode="json")


async def test_failed_turn_parity(
    postgres_repo: PostgresRepository, sqlite_repo: SQLiteRepository
) -> None:
    """Failed turns must serialize identical status, error_code, error, and empty response."""
    req = _make_request("Execute failing tool")
    error_code = "step_timeout"
    error_msg = "Execution timed out after 30.0s"

    await sqlite_repo.save_failed_turn("tool", req, error_code=error_code, error=error_msg)
    await postgres_repo.save_failed_turn("tool", req, error_code=error_code, error=error_msg)

    sq_rows = await sqlite_repo.list_turns(limit=1)
    pg_rows = await postgres_repo.list_turns(limit=1)

    sq_row = sq_rows[0]
    pg_row = pg_rows[0]

    assert sq_row["agent"] == pg_row["agent"] == "tool"
    assert sq_row["status"] == pg_row["status"] == str(TurnStatus.ERROR)
    assert sq_row["error_code"] == pg_row["error_code"] == error_code
    assert sq_row["error"] == pg_row["error"] == error_msg
    assert sq_row["response"] == pg_row["response"] == {}
    assert sq_row["request"] == pg_row["request"] == req.model_dump(mode="json")


async def test_json_lines_serialization_parity(
    postgres_repo: PostgresRepository, sqlite_repo: SQLiteRepository
) -> None:
    """Emulates `mangomas history` CLI output: serialized JSON lines must match."""
    req = _make_request("JSON parity test")
    resp = _make_response("JSON parity confirmed")

    await sqlite_repo.save_turn("chat", req, resp)
    await postgres_repo.save_turn("chat", req, resp)

    sq_rows = await sqlite_repo.list_turns(limit=1)
    pg_rows = await postgres_repo.list_turns(limit=1)

    # Serialize to JSON lines exactly as mangomas.cli.commands.chat:history does
    sq_line = json.dumps(sq_rows[0], ensure_ascii=False)
    pg_line = json.dumps(pg_rows[0], ensure_ascii=False)

    sq_parsed = json.loads(sq_line)
    pg_parsed = json.loads(pg_line)

    # Stripping auto-increment ID and timestamp yields identical JSON payloads
    assert _strip_dynamic_keys(sq_parsed) == _strip_dynamic_keys(pg_parsed)


async def test_multi_tenancy_isolation_parity(
    postgres_repo: PostgresRepository, sqlite_repo: SQLiteRepository
) -> None:
    """Tenant scoping must isolate rows identically across SQLite and PostgreSQL."""
    tenant_a = f"parity-tenant-a-{uuid.uuid4()}"
    tenant_b = f"parity-tenant-b-{uuid.uuid4()}"
    tenant_c = f"parity-tenant-c-{uuid.uuid4()}"

    # 1. Save turns under tenant_a
    set_tenant(tenant_a)
    assert get_tenant() == tenant_a
    for i in range(2):
        await sqlite_repo.save_turn("chat", _make_request(f"A-{i}"), _make_response(f"Resp-A-{i}"))
        await postgres_repo.save_turn(
            "chat", _make_request(f"A-{i}"), _make_response(f"Resp-A-{i}")
        )

    # 2. Save turns under tenant_b
    set_tenant(tenant_b)
    assert get_tenant() == tenant_b
    await sqlite_repo.save_turn("chat", _make_request("B-0"), _make_response("Resp-B-0"))
    await postgres_repo.save_turn("chat", _make_request("B-0"), _make_response("Resp-B-0"))

    # 3. Query under tenant_a
    set_tenant(tenant_a)
    sq_a = await sqlite_repo.list_turns()
    pg_a = await postgres_repo.list_turns()

    assert len(sq_a) == len(pg_a) == 2
    for sq_item, pg_item in zip(sq_a, pg_a, strict=True):
        assert _strip_dynamic_keys(sq_item) == _strip_dynamic_keys(pg_item)

    # 4. Query under tenant_b
    set_tenant(tenant_b)
    sq_b = await sqlite_repo.list_turns()
    pg_b = await postgres_repo.list_turns()

    assert len(sq_b) == len(pg_b) == 1
    assert _strip_dynamic_keys(sq_b[0]) == _strip_dynamic_keys(pg_b[0])

    # 5. Query under unused tenant_c
    set_tenant(tenant_c)
    assert get_tenant() == tenant_c
    sq_c = await sqlite_repo.list_turns()
    pg_c = await postgres_repo.list_turns()

    # Neither tenant's data leaks into tenant_c
    assert len(sq_c) == len(pg_c) == 0


async def test_pagination_and_ordering_parity(
    postgres_repo: PostgresRepository, sqlite_repo: SQLiteRepository
) -> None:
    """Ordering (newest first) and limit constraints must behave identically."""
    sq_ids: list[int] = []
    pg_ids: list[int] = []

    for i in range(4):
        sq_ids.append(
            await sqlite_repo.save_turn(
                "chat", _make_request(f"msg-{i}"), _make_response(f"reply-{i}")
            )
        )
        pg_ids.append(
            await postgres_repo.save_turn(
                "chat", _make_request(f"msg-{i}"), _make_response(f"reply-{i}")
            )
        )

    sq_rows = await sqlite_repo.list_turns(limit=2)
    pg_rows = await postgres_repo.list_turns(limit=2)

    assert len(sq_rows) == len(pg_rows) == 2

    # Newest first
    assert [r["id"] for r in sq_rows] == sorted(sq_ids, reverse=True)[:2]
    assert [r["id"] for r in pg_rows] == sorted(pg_ids, reverse=True)[:2]

    # Contents of latest 2 turns match
    assert sq_rows[0]["response"]["content"] == pg_rows[0]["response"]["content"] == "reply-3"
    assert sq_rows[1]["response"]["content"] == pg_rows[1]["response"]["content"] == "reply-2"
