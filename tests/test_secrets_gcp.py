"""Unit tests for :class:`GCPSecretManagerProvider`.

All tests use a constructor-injected fake client to avoid pulling the
``google-cloud-secret-manager`` SDK into the default test environment.
The error-path branches required to reach 100% line coverage on
``src/mangomas/secrets/gcp.py`` use a synthetic-exception trick: we
substitute the lazy-imported ``google.api_core.exceptions`` and
``google.auth.exceptions`` modules with stand-ins via
``monkeypatch.setitem(sys.modules, ...)`` so the ``except`` clauses
match without needing the real Google packages installed.
"""

from __future__ import annotations

import logging
import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest

from mangomas.composition import _resolve_llm_secrets
from mangomas.config import LLMSettings
from mangomas.secrets import GCPSecretManagerProvider, SecretsProvider, secrets_registry
from mangomas.secrets.gcp import _build_resource_path, _short_name

# ── Synthetic Google exception modules ────────────────────────────────────────


class _NotFound(Exception):
    pass


class _PermissionDenied(Exception):
    pass


class _Unauthenticated(Exception):
    pass


class _DeadlineExceeded(Exception):
    pass


class _GoogleAPIError(Exception):
    pass


class _OtherAPIError(_GoogleAPIError):
    pass


class _DefaultCredentialsError(Exception):
    pass


@pytest.fixture(autouse=True)
def _stub_google_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide stand-in ``google.api_core.exceptions`` + ``google.auth.exceptions``.

    The provider's ``get`` method imports these lazily; we substitute
    minimal modules so the ``except`` clauses bind to the synthetic
    exception classes defined above. No real Google SDK is required.
    """
    gax = types.ModuleType("google.api_core.exceptions")
    gax.NotFound = _NotFound  # type: ignore[attr-defined]
    gax.PermissionDenied = _PermissionDenied  # type: ignore[attr-defined]
    gax.Unauthenticated = _Unauthenticated  # type: ignore[attr-defined]
    gax.DeadlineExceeded = _DeadlineExceeded  # type: ignore[attr-defined]
    gax.GoogleAPIError = _GoogleAPIError  # type: ignore[attr-defined]

    gauth_exc = types.ModuleType("google.auth.exceptions")
    gauth_exc.DefaultCredentialsError = _DefaultCredentialsError  # type: ignore[attr-defined]

    # Ensure parent packages exist so the import path resolves.
    for pkg in ("google", "google.api_core", "google.auth", "google.cloud"):
        if pkg not in sys.modules:
            sys.modules[pkg] = types.ModuleType(pkg)

    monkeypatch.setitem(sys.modules, "google.api_core.exceptions", gax)
    monkeypatch.setitem(sys.modules, "google.auth.exceptions", gauth_exc)


# ── Fake SDK client ───────────────────────────────────────────────────────────


@dataclass
class _FakePayload:
    data: bytes


@dataclass
class _FakeResponse:
    payload: _FakePayload


@dataclass
class _FakeSecretClient:
    """Constructor-injected stand-in for SecretManagerServiceClient."""

    payloads: dict[str, bytes] = field(default_factory=dict)
    raises: BaseException | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def access_secret_version(self, *, request: dict[str, str], timeout: float) -> _FakeResponse:
        self.calls.append({"name": request["name"], "timeout": timeout})
        if self.raises is not None:
            raise self.raises
        return _FakeResponse(payload=_FakePayload(data=self.payloads[request["name"]]))


# ── Helpers ───────────────────────────────────────────────────────────────────


def _provider(
    *,
    project_id: str = "proj",
    timeout: float = 5.0,
    default_version: str = "latest",
    client: Any | None = None,
) -> GCPSecretManagerProvider:
    return GCPSecretManagerProvider(
        project_id=project_id,
        timeout_seconds=timeout,
        default_version=default_version,
        client=client,
    )


SECRET_VALUE: str = "resolved-via-vault"  # noqa: S105  test sentinel value
SHORT_NAME: str = "api-key"
FULL_PATH: str = f"projects/proj/secrets/{SHORT_NAME}/versions/latest"


# ── path/short helpers ────────────────────────────────────────────────────────


def test_build_resource_path_for_short_id() -> None:
    assert (
        _build_resource_path("k", project_id="proj", default_version="latest")
        == "projects/proj/secrets/k/versions/latest"
    )


def test_build_resource_path_passes_full_path_through() -> None:
    p = "projects/other/secrets/k/versions/3"
    assert _build_resource_path(p, project_id="proj", default_version="latest") == p


def test_short_name_for_short_id() -> None:
    assert _short_name("api-key") == "api-key"


def test_short_name_for_full_path() -> None:
    assert _short_name("projects/p/secrets/k/versions/3") == "k"


# ── happy path ────────────────────────────────────────────────────────────────


def test_get_returns_decoded_payload_for_short_id() -> None:
    client = _FakeSecretClient(payloads={FULL_PATH: SECRET_VALUE.encode("utf-8")})
    provider = _provider(client=client)

    assert provider.get(SHORT_NAME) == SECRET_VALUE
    assert client.calls == [{"name": FULL_PATH, "timeout": 5.0}]


def test_get_passes_full_path_through() -> None:
    full = "projects/other-proj/secrets/k/versions/2"
    client = _FakeSecretClient(payloads={full: b"value"})
    provider = _provider(client=client)

    assert provider.get(full) == "value"
    assert client.calls == [{"name": full, "timeout": 5.0}]


# ── error path branches ──────────────────────────────────────────────────────


def test_get_returns_none_on_not_found(caplog: pytest.LogCaptureFixture) -> None:
    client = _FakeSecretClient(raises=_NotFound("missing"))
    provider = _provider(client=client)

    with caplog.at_level(logging.DEBUG, logger="mangomas.secrets.gcp"):
        assert provider.get(SHORT_NAME) is None
    # NotFound is a debug-only log (operational signal lives elsewhere).
    assert any(r.levelno == logging.DEBUG for r in caplog.records)


def test_get_returns_none_on_missing_adc(caplog: pytest.LogCaptureFixture) -> None:
    client = _FakeSecretClient(raises=_DefaultCredentialsError("no ADC"))
    provider = _provider(client=client)

    with caplog.at_level(logging.ERROR, logger="mangomas.secrets.gcp"):
        assert provider.get(SHORT_NAME) is None
    assert any("ADC not configured" in r.getMessage() for r in caplog.records)


def test_get_returns_none_on_permission_denied(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _FakeSecretClient(raises=_PermissionDenied("denied"))
    provider = _provider(client=client)

    with caplog.at_level(logging.ERROR, logger="mangomas.secrets.gcp"):
        assert provider.get(SHORT_NAME) is None
    assert any("auth failure" in r.getMessage() for r in caplog.records)


def test_get_returns_none_on_unauthenticated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _FakeSecretClient(raises=_Unauthenticated("noauth"))
    provider = _provider(client=client)

    with caplog.at_level(logging.ERROR, logger="mangomas.secrets.gcp"):
        assert provider.get(SHORT_NAME) is None


def test_get_returns_none_on_deadline(caplog: pytest.LogCaptureFixture) -> None:
    client = _FakeSecretClient(raises=_DeadlineExceeded("slow"))
    provider = _provider(client=client)

    with caplog.at_level(logging.ERROR, logger="mangomas.secrets.gcp"):
        assert provider.get(SHORT_NAME) is None
    assert any("timed out" in r.getMessage() for r in caplog.records)


def test_get_returns_none_on_unknown_api_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _FakeSecretClient(raises=_OtherAPIError("boom"))
    provider = _provider(client=client)

    with caplog.at_level(logging.ERROR, logger="mangomas.secrets.gcp"):
        assert provider.get(SHORT_NAME) is None
    assert any("request failed" in r.getMessage() for r in caplog.records)


# ── safety: secret value never appears in logs ────────────────────────────────


def test_logs_never_contain_secret_value(caplog: pytest.LogCaptureFixture) -> None:
    """If we accidentally start logging the payload, this test fails."""
    sentinel = "DO-NOT-LEAK-THIS-VALUE"
    client = _FakeSecretClient(payloads={FULL_PATH: sentinel.encode("utf-8")})
    provider = _provider(client=client)

    with caplog.at_level(logging.DEBUG):
        result = provider.get(SHORT_NAME)
    assert result == sentinel
    for record in caplog.records:
        assert sentinel not in record.getMessage()
        for value in record.__dict__.values():
            assert sentinel not in str(value)


# ── protocol + composition end-to-end ────────────────────────────────────────


def test_provider_satisfies_secrets_protocol() -> None:
    assert isinstance(_provider(), SecretsProvider)


def test_resolve_llm_secrets_uses_gcp_provider_via_registry() -> None:
    client = _FakeSecretClient(payloads={FULL_PATH: SECRET_VALUE.encode("utf-8")})
    provider = _provider(client=client)
    with secrets_registry.scoped("gcp", provider):
        cfg = LLMSettings(
            api_key="inline-fallback",
            secret_ref=SHORT_NAME,
        )
        resolved = _resolve_llm_secrets(cfg, "gcp")
    assert resolved.api_key == SECRET_VALUE
