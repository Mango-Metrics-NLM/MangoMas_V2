"""Tests for the ``GET /history`` route and opt-in CORS (spec 0008 / M2)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from mangomas.agents import ChatAgent
from mangomas.api.app import create_app
from mangomas.config import get_settings
from mangomas.core import AgentContext, Orchestrator
from tests.fakes import FakeLLM, FakeRepository


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Isolate the CORS-env test's settings mutation from other tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _orchestrator(repo: FakeRepository | None) -> Orchestrator:
    orch = Orchestrator(AgentContext(llm=FakeLLM(), repo=repo))
    orch.register(ChatAgent())
    return orch


# ── GET /history ──────────────────────────────────────────────────────────────


def test_history_returns_persisted_turns() -> None:
    orch = _orchestrator(FakeRepository())
    app = create_app(orchestrator=orch)
    with TestClient(app) as client:
        client.post("/agents/chat/invoke", json={"messages": [{"role": "user", "content": "hi"}]})
        r = client.get("/history")
        assert r.status_code == 200
        turns = r.json()["turns"]
        assert len(turns) == 1
        assert turns[0]["agent"] == "chat"


def test_history_respects_limit() -> None:
    orch = _orchestrator(FakeRepository())
    app = create_app(orchestrator=orch)
    with TestClient(app) as client:
        for _ in range(3):
            client.post(
                "/agents/chat/invoke", json={"messages": [{"role": "user", "content": "hi"}]}
            )
        r = client.get("/history", params={"limit": 2})
        assert r.status_code == 200
        assert len(r.json()["turns"]) == 2


def test_history_empty_when_no_repo() -> None:
    app = create_app(orchestrator=_orchestrator(repo=None))
    with TestClient(app) as client:
        r = client.get("/history")
        assert r.status_code == 200
        assert r.json() == {"turns": []}


# ── Opt-in CORS ───────────────────────────────────────────────────────────────


def test_cors_absent_by_default() -> None:
    app = create_app(orchestrator=_orchestrator(FakeRepository()))
    with TestClient(app) as client:
        r = client.get("/health", headers={"Origin": "http://example.com"})
        assert r.status_code == 200
        assert "access-control-allow-origin" not in r.headers


def test_cors_present_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_API__CORS_ALLOW_ORIGINS", '["http://example.com"]')
    get_settings.cache_clear()
    app = create_app(orchestrator=_orchestrator(FakeRepository()))
    with TestClient(app) as client:
        r = client.get("/health", headers={"Origin": "http://example.com"})
        assert r.status_code == 200
        assert r.headers["access-control-allow-origin"] == "http://example.com"
