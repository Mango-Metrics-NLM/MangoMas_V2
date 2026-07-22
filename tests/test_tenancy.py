"""Tests for multi-tenancy Phase 1 — storage isolation (spec 0007 / ADR-0017)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mangomas.adapters.storage import SQLiteRepository
from mangomas.api.app import create_app
from mangomas.config import get_settings
from mangomas.core import AgentRequest, AgentResponse, Message, Orchestrator
from mangomas.tenancy import (
    DEFAULT_TENANT,
    get_tenant,
    resolve_tenant,
    sanitize_tenant,
    set_tenant,
    tenant_id,
)
from tests.constants import TENANT_A, TENANT_B, TENANT_HEADER

_MSG = {"messages": [{"role": "user", "content": "hi"}]}


def _req() -> AgentRequest:
    return AgentRequest(messages=[Message(role="user", content="hi")])


def _resp() -> AgentResponse:
    return AgentResponse(content="ok", agent="chat")


@pytest.fixture(autouse=True)
def _reset_tenant() -> Iterator[None]:
    """Keep the tenant ContextVar from leaking across tests (and into other files)."""
    tenant_id.set(None)
    yield
    tenant_id.set(None)


# ── tenancy.py unit ───────────────────────────────────────────────────────────


def test_sanitize_tenant_variants() -> None:
    assert sanitize_tenant(None) is None
    assert sanitize_tenant("   ") is None
    assert sanitize_tenant("!!!") is None  # all-disallowed → stripped to empty
    assert sanitize_tenant("  acme-1 ") == "acme-1"
    assert len(sanitize_tenant("x" * 200) or "") == 64  # clamped to MAX length


def test_resolve_and_get_tenant() -> None:
    assert resolve_tenant(None, "d") == "d"
    assert resolve_tenant("acme", "d") == "acme"
    assert get_tenant() == DEFAULT_TENANT  # unset → default
    set_tenant("z")
    assert get_tenant() == "z"


# ── SQLite storage isolation ──────────────────────────────────────────────────


async def test_sqlite_tenant_isolation() -> None:
    repo = SQLiteRepository(":memory:")
    set_tenant(TENANT_A)
    await repo.save_turn("chat", _req(), _resp())
    set_tenant(TENANT_B)
    await repo.save_turn("chat", _req(), _resp())
    await repo.save_turn("chat", _req(), _resp())

    b_turns = await repo.list_turns()
    set_tenant(TENANT_A)
    a_turns = await repo.list_turns()

    assert len(a_turns) == 1
    assert len(b_turns) == 2
    repo.close()


async def test_sqlite_disabled_path_uses_default_tenant() -> None:
    # No tenant set → get_tenant() == "default"; save + list both scope to it,
    # so behaviour is identical to the pre-tenancy single-tenant flow.
    repo = SQLiteRepository(":memory:")
    await repo.save_turn("chat", _req(), _resp())
    turns = await repo.list_turns()
    assert len(turns) == 1
    repo.close()


async def test_sqlite_migrates_pre_tenancy_db(tmp_path: Path) -> None:
    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE turns (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
        "agent TEXT NOT NULL, request TEXT NOT NULL, response TEXT NOT NULL)"
    )
    conn.execute("INSERT INTO turns (ts, agent, request, response) VALUES ('t','chat','{}','{}')")
    conn.commit()
    conn.close()

    repo = SQLiteRepository(f"sqlite:///{db}")  # __init__ migrates: ADD COLUMN tenant
    turns = await repo.list_turns()  # existing row adopted tenant='default' → visible
    assert len(turns) == 1
    repo.close()


# ── TenancyMiddleware end-to-end ──────────────────────────────────────────────


def test_tenancy_middleware_isolates_history(
    orchestrator: Orchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MANGOMAS_TENANCY__ENABLED", "true")
    get_settings.cache_clear()
    app = create_app(orchestrator=orchestrator)
    with TestClient(app) as client:
        client.post("/agents/chat/invoke", json=_MSG, headers={TENANT_HEADER: TENANT_A})
        a_history = client.get("/history", headers={TENANT_HEADER: TENANT_A})
        b_history = client.get("/history", headers={TENANT_HEADER: TENANT_B})
        assert len(a_history.json()["turns"]) == 1  # A sees its own turn
        assert len(b_history.json()["turns"]) == 0  # B sees nothing of A's
