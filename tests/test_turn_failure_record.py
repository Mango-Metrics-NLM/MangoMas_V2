"""A failed dispatch must leave a durable record (ADR-0031).

Before this, ``Orchestrator.dispatch`` reached ``repo.save_turn`` only after the
loop returned normally, so every failure path — ``MaxStepsExceeded``,
``StepTimeout``, a tool error, an LLM error — propagated past it and wrote
nothing. The only persistent log of the system's behaviour recorded successes,
which is the one shape an audit trail must not have.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from mangomas.adapters.storage import SQLiteRepository
from mangomas.adapters.storage._schema import TURN_SCHEMA_VERSION, TurnStatus
from mangomas.adapters.storage.base import FailureRecordingRepository, TurnRepository
from mangomas.core.agent import AgentRequest, AgentResponse, Message
from tests.constants import IN_MEMORY_SQLITE_URL


def _request() -> AgentRequest:
    return AgentRequest(messages=[Message(role="user", content="hello")])


def _response() -> AgentResponse:
    return AgentResponse(content="hi", agent="chat")


def test_sqlite_repository_satisfies_the_failure_recording_protocol() -> None:
    """The extension is a strict superset — the bare protocol still holds."""
    repo = SQLiteRepository(IN_MEMORY_SQLITE_URL)
    try:
        assert isinstance(repo, TurnRepository)
        assert isinstance(repo, FailureRecordingRepository)
    finally:
        repo.close()


async def test_a_failed_turn_is_persisted_with_its_error() -> None:
    """The failure row carries the status and the error that caused it."""
    repo = SQLiteRepository(IN_MEMORY_SQLITE_URL)
    try:
        row_id = await repo.save_failed_turn(
            "chat", _request(), error_code="llm_timeout", error="upstream took too long"
        )
        assert row_id > 0

        rows = await repo.list_turns()
        assert len(rows) == 1
        assert rows[0]["status"] == TurnStatus.ERROR
        assert rows[0]["error_code"] == "llm_timeout"
        assert "upstream took too long" in rows[0]["error"]
    finally:
        repo.close()


async def test_a_successful_turn_records_ok_status_and_no_error() -> None:
    """The other direction: success must not look like failure.

    Without this, ``save_failed_turn`` could write ``status='error'`` on every
    row and the test above would still pass.
    """
    repo = SQLiteRepository(IN_MEMORY_SQLITE_URL)
    try:
        await repo.save_turn("chat", _request(), _response())

        rows = await repo.list_turns()
        assert rows[0]["status"] == TurnStatus.OK
        assert rows[0]["error"] is None
        assert rows[0]["error_code"] is None
    finally:
        repo.close()


async def test_rows_carry_the_schema_version() -> None:
    """A record with no version cannot be evolved safely by any consumer."""
    repo = SQLiteRepository(IN_MEMORY_SQLITE_URL)
    try:
        await repo.save_turn("chat", _request(), _response())
        rows = await repo.list_turns()
        assert rows[0]["schema_version"] == TURN_SCHEMA_VERSION
    finally:
        repo.close()


async def test_a_pre_existing_table_is_migrated_in_place(tmp_path: Path) -> None:
    """A database written before this change keeps working and gains the columns.

    The additive-migration contract: existing deployments must not need a dump
    and reload, and their historical rows must read as successes, which is what
    they were.
    """
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE turns (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
        "agent TEXT NOT NULL, request TEXT NOT NULL, response TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO turns (ts, agent, request, response) VALUES ('t', 'chat', '{}', '{}')"
    )
    conn.commit()
    conn.close()

    repo = SQLiteRepository(f"sqlite:///{db}")
    try:
        rows = await repo.list_turns()
        assert len(rows) == 1
        assert rows[0]["status"] == TurnStatus.OK
        assert rows[0]["error"] is None
    finally:
        repo.close()
