"""Tests for the FastAPI app."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from mangomas.adapters.llm.lmstudio import LMStudioError
from mangomas.agents import ChatAgent
from mangomas.api import app as app_module
from mangomas.api.app import _error_status, create_app
from mangomas.core import AgentContext, Orchestrator
from mangomas.errors import (
    AgentNotFound,
    LLMBadResponse,
    LLMError,
    LLMTimeout,
    LLMUnavailable,
    MangomasError,
    PersistenceError,
    SecretsResolutionError,
)
from tests.constants import STUB_REPLY
from tests.fakes import FakeLLM, FakeRepository


def test_health(orchestrator: Orchestrator) -> None:
    app = create_app(orchestrator=orchestrator)
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


def test_list_agents(orchestrator: Orchestrator) -> None:
    app = create_app(orchestrator=orchestrator)
    with TestClient(app) as client:
        r = client.get("/agents")
        assert r.status_code == 200
        assert "chat" in r.json()["agents"]


def test_invoke_ok(orchestrator: Orchestrator) -> None:
    app = create_app(orchestrator=orchestrator)
    with TestClient(app) as client:
        r = client.post(
            "/agents/chat/invoke",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["agent"] == "chat"
        assert body["content"] == STUB_REPLY


def test_invoke_unknown_agent(
    orchestrator: Orchestrator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app(orchestrator=orchestrator)
    with caplog.at_level(logging.WARNING, logger="mangomas.api.app"), TestClient(app) as client:
        r = client.post(
            "/agents/nope/invoke",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 404
        body = r.json()
        assert body["error"] == "agent_not_found"
        assert "Request error agent_not_found" in caplog.text


def test_invoke_validation_error(orchestrator: Orchestrator) -> None:
    app = create_app(orchestrator=orchestrator)
    with TestClient(app) as client:
        r = client.post(
            "/agents/chat/invoke",
            json={"messages": [{"role": "bogus", "content": "hi"}]},
        )
        assert r.status_code == 422


# ── _error_status ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("exc", "expected_status"),
    [
        (AgentNotFound("x"), 404),
        (LLMTimeout("t"), 504),
        (LLMUnavailable("u"), 503),
        (LLMBadResponse("b"), 502),
        (LLMError("e"), 502),
        (PersistenceError("p"), 500),
        (SecretsResolutionError("k", provider="gcp"), 503),
        (MangomasError("m"), 500),
    ],
)
def test_error_status_mapping(exc: MangomasError, expected_status: int) -> None:
    assert _error_status(exc) == expected_status


def test_error_status_walks_mro_for_subclass() -> None:
    """LMStudioError is not directly in _ERROR_STATUS but inherits from LLMBadResponse."""
    exc = LMStudioError("bad parse")
    # LMStudioError → LLMBadResponse → 502  (requires MRO multi-step walk)
    assert _error_status(exc) == 502


def test_error_envelope_includes_detail(orchestrator: Orchestrator) -> None:
    """When exc.detail is non-empty the JSON envelope contains a ``detail`` key."""
    app = create_app(orchestrator=orchestrator)
    with TestClient(app) as client:
        # AgentNotFound has a non-empty detail field
        r = client.post(
            "/agents/no-such-agent/invoke",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
    body = r.json()
    assert "detail" in body


# ── Lifespan (startup / shutdown) ──────────────────────────────────────────────


def test_lifespan_startup_and_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lifespan wires the orchestrator on startup and closes adapters on shutdown."""
    fake_llm = FakeLLM()
    fake_repo = FakeRepository()
    orch = Orchestrator(AgentContext(llm=fake_llm, repo=fake_repo))
    orch.register(ChatAgent())

    monkeypatch.setattr(app_module, "build_orchestrator", lambda _settings: orch)
    monkeypatch.setattr(app_module, "configure_telemetry", lambda **_kw: None)

    fastapi_app = create_app()  # no injected orchestrator → uses _lifespan
    with TestClient(fastapi_app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        # While alive the app has a wired orchestrator
        assert fastapi_app.state.orchestrator is orch

    # After TestClient.__exit__ the lifespan finaliser has run.
    assert fake_llm.closed is True
    assert fake_repo.closed is True
