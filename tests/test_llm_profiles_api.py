"""HTTP surface of LLM provider profiles (spec-0039 / ADR-0038).

``GET /llm/profiles`` (R6: names, providers, models — never a secret) and the
pre-dispatch validation of ``AgentRequest.metadata["llm_profile"]`` on the
invoke route (R2/R5). The stream and workflow routes are covered in
``tests/test_streaming.py`` and ``tests/test_workflow_api.py``.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mangomas.agents import ChatAgent
from mangomas.api.app import create_app
from mangomas.composition import (
    LLM_PROFILE_INFO_EXTRAS_KEY,
    LLM_PROFILE_METADATA_KEY,
    LLM_PROFILES_EXTRAS_KEY,
)
from mangomas.composition.builder import _Orchestrator
from mangomas.config import get_settings
from mangomas.core import AgentContext
from tests.constants import (
    AUTH_SECRET_REF_ENV,
    AUTH_TOKEN,
    LLM_PROFILES_ROUTE,
    STUB_REPLY,
    TEST_LLM_PROFILE_MODEL,
    TEST_LLM_PROFILE_NAME,
    UNKNOWN_LLM_PROFILE_NAME,
)
from tests.fakes import FakeLLM

_PROFILE_REPLY = "profile-reply"
# Sentinels planted where a leak would originate: on the client object and in
# an extra, non-contract key of the summary. Neither may reach the wire.
_SECRET_SENTINEL = "sk-must-never-appear"  # noqa: S105  -- test sentinel, not a credential
_REF_SENTINEL = "VAULT_REF_MUST_NEVER_APPEAR"
_INVOKE_BODY = {"messages": [{"role": "user", "content": "hi"}]}


def _profile_body(name: str) -> dict[str, object]:
    return {**_INVOKE_BODY, "metadata": {LLM_PROFILE_METADATA_KEY: name}}


def _make_app(*, with_profile: bool) -> tuple[FastAPI, FakeLLM, FakeLLM]:
    default_llm = FakeLLM(reply=STUB_REPLY)
    profile_llm = FakeLLM(reply=_PROFILE_REPLY)
    profile_llm.api_key = _SECRET_SENTINEL  # type: ignore[attr-defined]
    extras: dict[str, object] = {}
    if with_profile:
        extras[LLM_PROFILES_EXTRAS_KEY] = {TEST_LLM_PROFILE_NAME: profile_llm}
        extras[LLM_PROFILE_INFO_EXTRAS_KEY] = {
            TEST_LLM_PROFILE_NAME: {
                "provider": "lmstudio",
                "model": TEST_LLM_PROFILE_MODEL,
                # Not part of the contract; the route must project, not dump.
                "secret_ref": _REF_SENTINEL,
            }
        }
    # The composition class, not the bare ``Orchestrator``: the R5 stamp is a
    # composition-root mixin, and this is the class ``create_app``'s lifespan
    # would hand the routes.
    orch = _Orchestrator(AgentContext(llm=default_llm, repo=None, extras=extras))
    orch.register(ChatAgent())
    return create_app(orchestrator=orch), default_llm, profile_llm


# ── GET /llm/profiles ─────────────────────────────────────────────────────────


def test_get_llm_profiles_empty_when_none_configured() -> None:
    """Default-OFF: a bare orchestrator (no extras at all) answers an empty roster."""
    app, _, _ = _make_app(with_profile=False)
    with TestClient(app) as client:
        r = client.get(LLM_PROFILES_ROUTE)
        assert r.status_code == 200
        assert r.json() == {"profiles": []}


def test_get_llm_profiles_lists_name_provider_model() -> None:
    app, _, _ = _make_app(with_profile=True)
    with TestClient(app) as client:
        r = client.get(LLM_PROFILES_ROUTE)
        assert r.status_code == 200
        assert r.json() == {
            "profiles": [
                {
                    "name": TEST_LLM_PROFILE_NAME,
                    "provider": "lmstudio",
                    "model": TEST_LLM_PROFILE_MODEL,
                }
            ]
        }


def test_get_llm_profiles_body_never_contains_secrets() -> None:
    """R6 negative test on the serialised body, not on the model definition."""
    app, _, _ = _make_app(with_profile=True)
    with TestClient(app) as client:
        text = client.get(LLM_PROFILES_ROUTE).text
    assert _SECRET_SENTINEL not in text
    assert _REF_SENTINEL not in text
    for forbidden in ("api_key", "secret_ref", "base_url", "credentials_path"):
        assert forbidden not in text, f"{forbidden!r} must never be serialised"


def test_get_llm_profiles_requires_auth_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """A data route, so it is guarded like invoke (ADR-0014) — and open by default."""
    monkeypatch.setenv("MANGOMAS_AUTH__ENABLED", "true")
    monkeypatch.setenv("MANGOMAS_AUTH__SECRET_REF", AUTH_SECRET_REF_ENV)
    monkeypatch.setenv(AUTH_SECRET_REF_ENV, AUTH_TOKEN)
    get_settings.cache_clear()
    app, _, _ = _make_app(with_profile=True)
    with TestClient(app) as client:
        assert client.get(LLM_PROFILES_ROUTE).status_code == 401
        ok = client.get(LLM_PROFILES_ROUTE, headers={"X-API-Key": AUTH_TOKEN})
        assert ok.status_code == 200


# ── POST /agents/{name}/invoke with metadata["llm_profile"] ───────────────────


def test_invoke_with_profile_uses_profile_client_and_stamps_response_metadata() -> None:
    app, default_llm, profile_llm = _make_app(with_profile=True)
    with TestClient(app) as client:
        r = client.post("/agents/chat/invoke", json=_profile_body(TEST_LLM_PROFILE_NAME))
        assert r.status_code == 200
        body = r.json()
        assert body["content"] == _PROFILE_REPLY
        assert body["metadata"][LLM_PROFILE_METADATA_KEY] == TEST_LLM_PROFILE_NAME
    assert profile_llm.calls
    assert not default_llm.calls


def test_invoke_without_profile_has_no_llm_profile_key() -> None:
    """Default-OFF on the wire: the key is absent, not null."""
    app, default_llm, profile_llm = _make_app(with_profile=True)
    with TestClient(app) as client:
        r = client.post("/agents/chat/invoke", json=_INVOKE_BODY)
        assert r.status_code == 200
        assert r.json()["content"] == STUB_REPLY
        assert LLM_PROFILE_METADATA_KEY not in r.json()["metadata"]
        assert LLM_PROFILE_METADATA_KEY not in r.text
    assert default_llm.calls
    assert not profile_llm.calls


def test_invoke_unknown_profile_is_400_config_error() -> None:
    app, default_llm, profile_llm = _make_app(with_profile=True)
    with TestClient(app) as client:
        r = client.post("/agents/chat/invoke", json=_profile_body(UNKNOWN_LLM_PROFILE_NAME))
        assert r.status_code == 400
        assert r.json()["error"] == "config_error"
        assert UNKNOWN_LLM_PROFILE_NAME in r.json()["message"]
    assert not default_llm.calls, "no agent ran"
    assert not profile_llm.calls
