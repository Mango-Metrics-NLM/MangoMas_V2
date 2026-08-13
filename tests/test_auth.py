"""Tests for the opt-in API authentication seam (spec 0010 / ADR-0014)."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from mangomas.api.app import create_app
from mangomas.api.auth import resolve_auth_state
from mangomas.composition import ensure_secrets_provider
from mangomas.config import AuthSettings, SecretsSettings, Settings, get_settings
from mangomas.core import Orchestrator
from mangomas.registry import Registry
from mangomas.secrets.provider import SecretsProvider
from tests.constants import AUTH_SECRET_REF_ENV, AUTH_TOKEN

_MSG = {"messages": [{"role": "user", "content": "hi"}]}
_AGENT_GRAPH = json.dumps({"name": "t", "root": {"kind": "agent", "agent": "chat"}})


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


def test_auth_settings_enabled_requires_secret_ref() -> None:
    """enabled=True without a secret_ref is rejected — auth is never keyless."""
    with pytest.raises(ValidationError, match="secret_ref"):
        AuthSettings(enabled=True)


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


# ── Secrets-provider registration ordering (regression) ───────────────────────
#
# These use a *fresh* Registry swapped into the composition module rather than
# mutating the process-wide singleton: the singleton is seeded at import time
# and shared by every test in the session, so deleting from it would make these
# order-dependent and would need private-attribute access.


@pytest.fixture
def clean_secrets_registry(monkeypatch: pytest.MonkeyPatch) -> Registry[SecretsProvider]:
    """An empty registry swapped in for the process-wide singleton.

    Both ``composition`` and ``api.auth`` bind the singleton by
    ``from ... import secrets_registry``, so each holds its own name for the
    same object and **both** must be rebound — patching one leaves the other
    reading the real registry, which silently makes the assertion below pass
    for the wrong reason.

    A fresh instance rather than mutating the singleton: it is seeded at import
    time and shared across the whole session, so registering a stub into it
    would leak into every later test.
    """
    registry: Registry[SecretsProvider] = Registry("secrets")
    monkeypatch.setattr("mangomas.composition.secrets_registry", registry)
    monkeypatch.setattr("mangomas.api.auth.secrets_registry", registry)
    return registry


@pytest.mark.usefixtures("clean_secrets_registry")
def test_create_app_resolves_the_token_for_a_cloud_secrets_provider(
    orchestrator: Orchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``create_app`` must resolve a real token when the backend is a cloud one.

    Regression, and asserted end-to-end through ``create_app`` on purpose: an
    earlier version of this test called ``ensure_secrets_provider`` directly and
    passed even with the fix reverted, because the defect was never in that
    helper — it was in *when* it runs.

    The ``gcp`` provider used to be registered only inside
    ``build_orchestrator``, which the FastAPI lifespan calls strictly *after*
    app construction. ``resolve_auth_state`` therefore looked up an
    unregistered provider, fell closed to ``expected_token=None``, and 401'd
    every request of a correctly configured GCP + auth deployment. Local runs
    never saw it: the default ``env`` backend is seeded at import time, so only
    a cloud provider reached the gap.
    """
    monkeypatch.setenv("MANGOMAS_AUTH__ENABLED", "true")
    monkeypatch.setenv("MANGOMAS_AUTH__SECRET_REF", AUTH_SECRET_REF_ENV)
    monkeypatch.setenv("MANGOMAS_SECRETS__PROVIDER", "gcp")
    monkeypatch.setenv("MANGOMAS_SECRETS__PROJECT_ID", "test-project")
    get_settings.cache_clear()

    # Stand in for Secret Manager so the test needs no SDK, credentials or network.
    class _StubCloudProvider:
        def get(self, ref: str) -> str | None:
            return AUTH_TOKEN if ref == AUTH_SECRET_REF_ENV else None

    monkeypatch.setattr(
        "mangomas.composition._build_gcp_secrets_provider",
        lambda _cfg: _StubCloudProvider(),
    )

    app = create_app(orchestrator=orchestrator)

    assert app.state.auth.enabled is True
    assert app.state.auth.expected_token == AUTH_TOKEN, (
        "create_app resolved no token — the secrets provider was not registered "
        "before resolve_auth_state ran"
    )


def test_ensure_secrets_provider_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, clean_secrets_registry: Registry[SecretsProvider]
) -> None:
    """Called from both ``create_app`` and ``build_orchestrator``, so a second
    call must not raise or replace the registered instance."""
    monkeypatch.setenv("MANGOMAS_SECRETS__PROVIDER", "gcp")
    monkeypatch.setenv("MANGOMAS_SECRETS__PROJECT_ID", "test-project")
    get_settings.cache_clear()

    cfg = get_settings().secrets
    ensure_secrets_provider(cfg)
    first = clean_secrets_registry.get("gcp")
    ensure_secrets_provider(cfg)

    assert clean_secrets_registry.get("gcp") is first


def test_ensure_secrets_provider_ignores_the_env_backend(
    monkeypatch: pytest.MonkeyPatch, clean_secrets_registry: Registry[SecretsProvider]
) -> None:
    """``env`` is seeded at import time, so the helper must register nothing."""
    monkeypatch.setenv("MANGOMAS_SECRETS__PROVIDER", "env")
    get_settings.cache_clear()

    ensure_secrets_provider(get_settings().secrets)

    assert clean_secrets_registry.available() == []


@pytest.mark.usefixtures("clean_secrets_registry")
def test_unregistered_provider_logs_before_failing_closed(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failing closed must not fail silently.

    This branch rejects every request for the life of the process. It used to
    emit nothing, which is why a provider-ordering bug that 401'd a correctly
    configured deployment was invisible until someone read the code.
    """
    monkeypatch.setenv("MANGOMAS_AUTH__ENABLED", "true")
    monkeypatch.setenv("MANGOMAS_AUTH__SECRET_REF", AUTH_SECRET_REF_ENV)
    monkeypatch.setenv("MANGOMAS_SECRETS__PROVIDER", "gcp")
    get_settings.cache_clear()
    caplog.set_level(logging.ERROR, logger="mangomas.api.auth")

    state = resolve_auth_state(get_settings())

    assert state.expected_token is None
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "not registered" in logged
    assert any(getattr(r, "secrets_provider", None) == "gcp" for r in caplog.records)


def test_unresolvable_secret_ref_logs_before_failing_closed(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other silent path: the provider exists but the ref resolves to None."""
    monkeypatch.setenv("MANGOMAS_AUTH__ENABLED", "true")
    monkeypatch.setenv("MANGOMAS_AUTH__SECRET_REF", "UNSET_TOKEN_VAR_XYZ")
    monkeypatch.delenv("UNSET_TOKEN_VAR_XYZ", raising=False)
    get_settings.cache_clear()
    caplog.set_level(logging.ERROR, logger="mangomas.api.auth")

    state = resolve_auth_state(get_settings())

    assert state.expected_token is None
    assert "resolved to nothing" in " ".join(r.getMessage() for r in caplog.records)
