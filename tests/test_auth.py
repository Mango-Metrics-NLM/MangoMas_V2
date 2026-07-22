"""Tests for the opt-in API authentication seam (spec 0010 / ADR-0014)."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mangomas.api.app import create_app
from mangomas.api.auth import resolve_auth_state
from mangomas.config import AuthSettings, SecretsSettings, Settings, get_settings
from mangomas.core import Orchestrator
from tests.constants import AUTH_SECRET_REF_ENV, AUTH_TOKEN

_MSG = {"messages": [{"role": "user", "content": "hi"}]}
_AGENT_GRAPH = json.dumps({"name": "t", "root": {"kind": "agent", "agent": "chat"}})


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def auth_app(orchestrator: Orchestrator, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """An app with auth enabled and the expected token resolved from the env provider."""
    monkeypatch.setenv("MANGOMAS_AUTH__ENABLED", "true")
    monkeypatch.setenv("MANGOMAS_AUTH__SECRET_REF", AUTH_SECRET_REF_ENV)
    monkeypatch.setenv(AUTH_SECRET_REF_ENV, AUTH_TOKEN)
    get_settings.cache_clear()
    return create_app(orchestrator=orchestrator)


# ── Disabled (default) ────────────────────────────────────────────────────────


def test_disabled_allows_guarded_routes(orchestrator: Orchestrator) -> None:
    app = create_app(orchestrator=orchestrator)
    with TestClient(app) as client:
        assert client.post("/agents/chat/invoke", json=_MSG).status_code == 200


# ── Enabled ───────────────────────────────────────────────────────────────────


def test_enabled_rejects_missing_token(auth_app: FastAPI) -> None:
    with TestClient(auth_app) as client:
        r = client.post("/agents/chat/invoke", json=_MSG)
        assert r.status_code == 401
        assert r.json()["error"] == "authentication_error"


def test_enabled_accepts_bearer(auth_app: FastAPI) -> None:
    with TestClient(auth_app) as client:
        r = client.post(
            "/agents/chat/invoke", json=_MSG, headers={"Authorization": f"Bearer {AUTH_TOKEN}"}
        )
        assert r.status_code == 200


def test_enabled_accepts_api_key_header(auth_app: FastAPI) -> None:
    with TestClient(auth_app) as client:
        r = client.post("/agents/chat/invoke", json=_MSG, headers={"X-API-Key": AUTH_TOKEN})
        assert r.status_code == 200


def test_enabled_rejects_wrong_token(auth_app: FastAPI) -> None:
    with TestClient(auth_app) as client:
        r = client.post(
            "/agents/chat/invoke", json=_MSG, headers={"Authorization": "Bearer wrong-token"}
        )
        assert r.status_code == 401


def test_workflow_route_is_guarded(auth_app: FastAPI) -> None:
    with TestClient(auth_app) as client:
        r = client.post("/workflows/validate", json={"definition": _AGENT_GRAPH})
        assert r.status_code == 401
        r_ok = client.post(
            "/workflows/validate",
            json={"definition": _AGENT_GRAPH},
            headers={"X-API-Key": AUTH_TOKEN},
        )
        assert r_ok.status_code == 200


def test_probes_stay_open_when_enabled(auth_app: FastAPI) -> None:
    with TestClient(auth_app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code != 401


def test_fail_closed_when_secret_unresolved(
    orchestrator: Orchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MANGOMAS_AUTH__ENABLED", "true")
    monkeypatch.setenv("MANGOMAS_AUTH__SECRET_REF", "UNSET_TOKEN_VAR_XYZ")
    monkeypatch.delenv("UNSET_TOKEN_VAR_XYZ", raising=False)
    get_settings.cache_clear()
    app = create_app(orchestrator=orchestrator)
    with TestClient(app) as client:
        # Even a "correct-looking" token is rejected — the server has no key.
        r = client.post(
            "/agents/chat/invoke", json=_MSG, headers={"Authorization": f"Bearer {AUTH_TOKEN}"}
        )
        assert r.status_code == 401


# ── resolve_auth_state unit branches ──────────────────────────────────────────


def test_resolve_auth_state_disabled() -> None:
    state = resolve_auth_state(Settings())
    assert state.enabled is False
    assert state.expected_token is None


def test_resolve_auth_state_unregistered_provider_fails_closed() -> None:
    settings = Settings(
        auth=AuthSettings(enabled=True, secret_ref="X"),  # noqa: S106 — a ref, not a secret
        secrets=SecretsSettings(provider="bogus"),
    )
    state = resolve_auth_state(settings)
    assert state.enabled is True
    assert state.expected_token is None
