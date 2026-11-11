"""Tests for ``scripts/verify_gcp_prerequisites.py`` (spec-0049).

Offline and dependency-free: ``google-auth`` is replaced by an injected
:class:`GoogleAuthBackend` of fakes, so the module collects and runs under
``make install`` (``[dev]`` only), which is what CI installs. Settings are
built with ``_env_file=None`` and a scrubbed environment so a developer's
``.env`` or exported ``MANGOMAS_*`` variables cannot change an outcome.
"""

from __future__ import annotations

import json
import logging
import socket
from collections.abc import Iterator
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

import pytest

from mangomas.config import (
    DBSettings,
    EmbeddingSettings,
    LLMSettings,
    SecretsSettings,
    Settings,
    TelemetrySettings,
)
from tests._script_loader import load_script_module

_SCRIPT = "verify_gcp_prerequisites.py"
_PROJECT = "demo-project"
_ACCOUNT = "billingAccounts/000000-AAAAAA-111111"
_SA_EMAIL = "demo-sa@demo-project.iam.gserviceaccount.com"
_SECRET_PASSWORD = "s3cr3t-do-not-echo"  # noqa: S105 -- sentinel proving redaction


@pytest.fixture
def pf() -> ModuleType:
    return load_script_module(_SCRIPT)


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove every variable that could feed project or provider resolution."""
    import os  # noqa: PLC0415

    for name in [n for n in os.environ if n.startswith("MANGOMAS_")]:
        monkeypatch.delenv(name)
    for name in ("GOOGLE_CLOUD_PROJECT", "CLOUDSDK_CORE_PROJECT"):
        monkeypatch.delenv(name, raising=False)
    yield


def _settings(**kwargs: Any) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg]


def _gcp_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "llm": LLMSettings(provider="vertex", project_id=_PROJECT),
        "secrets": SecretsSettings(provider="gcp", project_id=_PROJECT),
        "telemetry": TelemetrySettings(exporter="gcp"),
    }
    base.update(overrides)
    return _settings(**base)


# ── Fakes ─────────────────────────────────────────────────────────────────────


class _CredError(Exception):
    pass


class _RefreshError(Exception):
    pass


@dataclass
class _Creds:
    service_account_email: str | None = _SA_EMAIL
    refresh_exc: Exception | None = None
    refreshed: int = 0

    def refresh(self, _request: object) -> None:
        self.refreshed += 1
        if self.refresh_exc is not None:
            raise self.refresh_exc


@dataclass
class _Response:
    status_code: int
    body: Any = field(default_factory=dict)

    def json(self) -> Any:
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


@dataclass
class _Session:
    """Routes GET URLs (by suffix) to canned responses; records calls."""

    routes: dict[str, _Response | Exception] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def get(self, url: str, *, timeout: float) -> _Response:
        assert timeout > 0
        self.calls.append(url)
        for suffix, outcome in self.routes.items():
            if url.endswith(suffix):
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
        return _Response(404, {"error": "unrouted"})


def _backend(
    pf: ModuleType,
    *,
    creds: _Creds | None = None,
    default_exc: Exception | None = None,
    adc_project: str | None = None,
    session: _Session | None = None,
) -> Any:
    def default(**_kw: Any) -> tuple[Any, str | None]:
        if default_exc is not None:
            raise default_exc
        return (creds or _Creds()), adc_project

    return pf.GoogleAuthBackend(
        default=default,
        make_request=object,
        make_session=lambda _c: session or _Session(),
        credentials_error=_CredError,
        refresh_error=_RefreshError,
    )


def _healthy_session(apis: tuple[str, ...] = ()) -> _Session:
    routes: dict[str, _Response | Exception] = {
        f"projects/{_PROJECT}/billingInfo": _Response(
            200, {"billingEnabled": True, "billingAccountName": _ACCOUNT}
        ),
        _ACCOUNT: _Response(200, {"open": True}),
    }
    routes.update({f"services/{a}": _Response(200, {"state": "ENABLED"}) for a in apis})
    return _Session(routes)


# ── ADC ───────────────────────────────────────────────────────────────────────


def test_adc_without_google_auth_fails_with_install_hint(pf: ModuleType) -> None:
    outcome = pf.check_adc_credentials(None)
    assert outcome.result.status is pf.CheckStatus.FAIL
    assert "mangomas[vertex]" in outcome.result.remediation
    assert outcome.credentials is None


def test_adc_success_reports_identity_and_refreshes(pf: ModuleType) -> None:
    creds = _Creds()
    outcome = pf.check_adc_credentials(_backend(pf, creds=creds, adc_project="  adc-proj  "))
    assert outcome.result.status is pf.CheckStatus.PASS
    assert _SA_EMAIL in outcome.result.message
    assert creds.refreshed == 1
    assert outcome.adc_project == "adc-proj"  # whitespace stripped


def test_adc_identity_falls_back_to_type_name(pf: ModuleType) -> None:
    outcome = pf.check_adc_credentials(_backend(pf, creds=_Creds(service_account_email=None)))
    assert "_Creds" in outcome.result.message


@pytest.mark.parametrize(
    ("kwargs", "fragment", "remedy"),
    [
        ({"default_exc": _CredError("none")}, "not found", "application-default login"),
        ({"default_exc": PermissionError("perm")}, "error discovering", "permissions"),
        ({"creds": _Creds(refresh_exc=_RefreshError("old"))}, "expired or revoked", "login"),
        ({"creds": _Creds(refresh_exc=OSError("reset"))}, "network error", "oauth2.googleapis.com"),
    ],
)
def test_adc_failure_modes_are_fail_rows_not_crashes(
    pf: ModuleType, kwargs: dict[str, Any], fragment: str, remedy: str
) -> None:
    outcome = pf.check_adc_credentials(_backend(pf, **kwargs))
    assert outcome.result.status is pf.CheckStatus.FAIL
    assert fragment in outcome.result.message.lower()
    assert remedy in outcome.result.remediation
    assert outcome.credentials is None


# ── Project resolution ────────────────────────────────────────────────────────


def test_project_precedence_flag_beats_settings(pf: ModuleType) -> None:
    assert pf.resolve_project(_gcp_settings(), "flag-proj", "adc") == "flag-proj"


@pytest.mark.parametrize(
    ("settings_kwargs", "expected"),
    [
        ({"llm": LLMSettings(project_id="llm-p")}, "llm-p"),
        ({"embeddings": EmbeddingSettings(project_id="emb-p")}, "emb-p"),
        ({"secrets": SecretsSettings(project_id="sec-p")}, "sec-p"),
    ],
)
def test_project_resolves_from_each_settings_group(
    pf: ModuleType, settings_kwargs: dict[str, Any], expected: str
) -> None:
    assert pf.resolve_project(_settings(**settings_kwargs), None, "adc-p") == expected


def test_project_falls_back_to_sdk_env_then_adc(
    pf: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert pf.resolve_project(_settings(), "   ", "adc-p") == "adc-p"  # blank flag ignored
    monkeypatch.setenv("CLOUDSDK_CORE_PROJECT", "sdk-p")
    assert pf.resolve_project(_settings(), None, "adc-p") == "sdk-p"


def test_gcp_project_check_pass_and_fail(pf: ModuleType) -> None:
    assert pf.check_gcp_project(_PROJECT).status is pf.CheckStatus.PASS
    missing = pf.check_gcp_project(None)
    assert missing.status is pf.CheckStatus.FAIL
    assert "--project" in missing.remediation


@pytest.mark.parametrize(
    "bad_project",
    ["bad_project", "-invalid", "project-", "123proj", "sh", "a" * 31],
)
def test_project_id_regex_validation(pf: ModuleType, bad_project: str) -> None:
    result = pf.check_gcp_project(bad_project)
    assert result.status is pf.CheckStatus.FAIL
    assert "Invalid GCP project ID" in result.message
    assert "gcloud config set project" in result.remediation


# ── Required APIs derivation ──────────────────────────────────────────────────


def test_required_apis_follow_configured_providers(pf: ModuleType) -> None:
    assert pf.required_apis(_gcp_settings()) == [
        "aiplatform.googleapis.com",
        "cloudtrace.googleapis.com",
        "secretmanager.googleapis.com",
    ]


def test_local_providers_require_no_api(pf: ModuleType) -> None:
    assert pf.required_apis(_settings()) == []


def test_enabled_vertex_embeddings_require_aiplatform(pf: ModuleType) -> None:
    s = _settings(embeddings=EmbeddingSettings(enabled=True, provider="vertex"))
    assert pf.required_apis(s) == ["aiplatform.googleapis.com"]


def test_disabled_embeddings_add_nothing(pf: ModuleType) -> None:
    s = _settings(embeddings=EmbeddingSettings(enabled=False, provider="vertex"))
    assert pf.required_apis(s) == []


def test_extra_apis_are_deduplicated_and_blank_dropped(pf: ModuleType) -> None:
    got = pf.required_apis(_settings(), ["sqladmin.googleapis.com", " ", "sqladmin.googleapis.com"])
    assert got == ["sqladmin.googleapis.com"]


# ── Billing ───────────────────────────────────────────────────────────────────


def test_billing_skips_without_session_or_project(pf: ModuleType) -> None:
    assert pf.check_billing(None, _PROJECT, 1.0).status is pf.CheckStatus.SKIP
    assert pf.check_billing(_Session(), None, 1.0).status is pf.CheckStatus.SKIP


def test_billing_open_account_passes(pf: ModuleType) -> None:
    result = pf.check_billing(_healthy_session(), _PROJECT, 1.0)
    assert result.status is pf.CheckStatus.PASS
    assert result.details["billing_account"] == _ACCOUNT


def test_closed_account_fails_even_though_project_reports_enabled(pf: ModuleType) -> None:
    """The observed field failure: billingEnabled=true, account OPEN=False."""
    session = _healthy_session()
    session.routes[_ACCOUNT] = _Response(200, {"open": False})
    result = pf.check_billing(session, _PROJECT, 1.0)
    assert result.status is pf.CheckStatus.FAIL
    assert "CLOSED" in result.message
    assert "PERMISSION_DENIED" in result.message


def test_billing_disabled_project_fails_with_console_link(pf: ModuleType) -> None:
    session = _Session({f"{_PROJECT}/billingInfo": _Response(200, {"billingEnabled": False})})
    result = pf.check_billing(session, _PROJECT, 1.0)
    assert result.status is pf.CheckStatus.FAIL
    assert f"project={_PROJECT}" in result.remediation


def test_billing_forbidden_is_skip_not_fail(pf: ModuleType) -> None:
    session = _Session({f"{_PROJECT}/billingInfo": _Response(403, {"error": "denied"})})
    assert pf.check_billing(session, _PROJECT, 1.0).status is pf.CheckStatus.SKIP


def test_unreadable_account_state_skips_with_caveat(pf: ModuleType) -> None:
    session = _healthy_session()
    session.routes[_ACCOUNT] = _Response(403, ValueError("not json"))
    result = pf.check_billing(session, _PROJECT, 1.0)
    assert result.status is pf.CheckStatus.SKIP
    assert "not readable" in result.message


def test_billing_account_malformed_fails(pf: ModuleType) -> None:
    session = _Session(
        {
            f"projects/{_PROJECT}/billingInfo": _Response(
                200,
                {"billingEnabled": True, "billingAccountName": "billingAccounts/invalid-format"},
            )
        }
    )
    result = pf.check_billing(session, _PROJECT, 1.0)
    assert result.status is pf.CheckStatus.FAIL
    assert "Malformed or missing billing account name" in result.message


@pytest.mark.parametrize("status_code", [404, 500])
def test_billing_account_http_error_fails(pf: ModuleType, status_code: int) -> None:
    session = _healthy_session()
    session.routes[_ACCOUNT] = _Response(status_code, {"error": "backend error"})
    result = pf.check_billing(session, _PROJECT, 1.0)
    assert result.status is pf.CheckStatus.FAIL
    assert f"Billing account API returned HTTP {status_code}." in result.message


def test_billing_account_missing_open_fails(pf: ModuleType) -> None:
    session = _healthy_session()
    session.routes[_ACCOUNT] = _Response(200, {})  # "open" key omitted
    result = pf.check_billing(session, _PROJECT, 1.0)
    assert result.status is pf.CheckStatus.FAIL
    assert "CLOSED" in result.message


@pytest.mark.parametrize("outcome", [_Response(500, {"error": "boom"}), ConnectionError("dns")])
def test_billing_server_or_transport_error_fails(
    pf: ModuleType, outcome: _Response | Exception
) -> None:
    session = _Session({f"{_PROJECT}/billingInfo": outcome})
    assert pf.check_billing(session, _PROJECT, 1.0).status is pf.CheckStatus.FAIL


# ── Required APIs check ───────────────────────────────────────────────────────

_AIP = "aiplatform.googleapis.com"
_SM = "secretmanager.googleapis.com"


def test_apis_skip_when_none_required_or_no_session(pf: ModuleType) -> None:
    assert pf.check_required_apis(_Session(), _PROJECT, [], 1.0).status is pf.CheckStatus.SKIP
    assert pf.check_required_apis(None, _PROJECT, [_AIP], 1.0).status is pf.CheckStatus.SKIP


def test_all_enabled_apis_pass(pf: ModuleType) -> None:
    result = pf.check_required_apis(_healthy_session((_AIP, _SM)), _PROJECT, [_AIP, _SM], 1.0)
    assert result.status is pf.CheckStatus.PASS


def test_disabled_api_fails_with_enable_command(pf: ModuleType) -> None:
    session = _healthy_session((_AIP,))
    session.routes[f"services/{_SM}"] = _Response(200, {"state": "DISABLED"})
    result = pf.check_required_apis(session, _PROJECT, [_AIP, _SM], 1.0)
    assert result.status is pf.CheckStatus.FAIL
    assert result.details["disabled"] == [_SM]
    assert f"gcloud services enable {_SM} --project {_PROJECT}" == result.remediation


def test_api_probe_forbidden_skips(pf: ModuleType) -> None:
    session = _Session({f"services/{_AIP}": _Response(403, {})})
    assert pf.check_required_apis(session, _PROJECT, [_AIP], 1.0).status is pf.CheckStatus.SKIP


def test_api_probe_transport_error_fails(pf: ModuleType) -> None:
    session = _Session({f"services/{_AIP}": TimeoutError("slow")})
    assert pf.check_required_apis(session, _PROJECT, [_AIP], 1.0).status is pf.CheckStatus.FAIL


def test_non_object_json_body_counts_as_not_enabled(pf: ModuleType) -> None:
    session = _Session({f"services/{_AIP}": _Response(200, ["unexpected"])})
    assert pf.check_required_apis(session, _PROJECT, [_AIP], 1.0).status is pf.CheckStatus.FAIL


@pytest.mark.parametrize(
    "bad_api",
    ["invalid;api", "api/traversal", "aiplatform..googleapis.com", "-aiplatform.googleapis.com"],
)
def test_api_name_regex_validation(pf: ModuleType, bad_api: str) -> None:
    session = _healthy_session()
    result = pf.check_required_apis(session, _PROJECT, [bad_api], 1.0)
    assert result.status is pf.CheckStatus.FAIL
    assert f"Invalid API service name: {bad_api!r}" in result.message


def test_required_apis_evaluates_all_and_treats_500_as_probe_error(pf: ModuleType) -> None:
    session = _healthy_session()
    session.routes[f"services/{_AIP}"] = _Response(500, {"error": "server error"})
    session.routes[f"services/{_SM}"] = _Response(200, {"state": "DISABLED"})
    result = pf.check_required_apis(session, _PROJECT, [_AIP, _SM], 1.0)
    assert result.status is pf.CheckStatus.FAIL
    assert "Service Usage probe failed for 1 API(s)." in result.message
    assert any(err.get("api") == _AIP for err in result.details.get("probe_errors", []))


def test_required_apis_forbidden_and_disabled_reports_disabled_failure(pf: ModuleType) -> None:
    session = _healthy_session()
    session.routes[f"services/{_AIP}"] = _Response(403, {})
    session.routes[f"services/{_SM}"] = _Response(200, {"state": "DISABLED"})
    result = pf.check_required_apis(session, _PROJECT, [_AIP, _SM], 1.0)
    assert result.status is pf.CheckStatus.FAIL
    assert "Disabled API(s): secretmanager.googleapis.com" in result.message


# ── Postgres endpoint ─────────────────────────────────────────────────────────


@pytest.fixture
def listening_port() -> Iterator[int]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        yield int(srv.getsockname()[1])


@pytest.fixture
def closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])  # released on exit: nothing listens


def test_endpoint_skips_when_no_url(pf: ModuleType) -> None:
    assert pf.check_postgres_endpoint(None, 1.0).status is pf.CheckStatus.SKIP


def test_endpoint_reachable_passes_without_echoing_password(
    pf: ModuleType, listening_port: int
) -> None:
    url = f"postgresql://user:{_SECRET_PASSWORD}@127.0.0.1:{listening_port}/db"
    result = pf.check_postgres_endpoint(url, 1.0)
    assert result.status is pf.CheckStatus.PASS
    assert "proxy or local Postgres" in result.message
    assert _SECRET_PASSWORD not in json.dumps(result.details) + result.message


def test_postgres_endpoint_password_with_hash(pf: ModuleType, listening_port: int) -> None:
    pw_with_hash = "p#ss#w0rd#special"
    url = f"postgresql://user:{pw_with_hash}@127.0.0.1:{listening_port}/db"
    result = pf.check_postgres_endpoint(url, 1.0)
    assert result.status is pf.CheckStatus.PASS
    assert "proxy or local Postgres" in result.message
    assert pw_with_hash not in json.dumps(result.details) + result.message


def test_postgres_endpoint_does_not_leak_on_parse_error(pf: ModuleType) -> None:
    url = f"postgresql://user:{_SECRET_PASSWORD}@127.0.0.1:badport/db"
    result = pf.check_postgres_endpoint(url, 1.0)
    assert result.status is pf.CheckStatus.FAIL
    assert "does not parse" in result.message.lower()
    assert _SECRET_PASSWORD not in json.dumps(result.details) + result.message


def test_postgres_endpoint_password_with_query_char_redacted(
    pf: ModuleType, listening_port: int
) -> None:
    pw = "secret?token"
    url = f"postgresql://user:{pw}@127.0.0.1:{listening_port}/db"
    result = pf.check_postgres_endpoint(url, 1.0)
    assert result.status is pf.CheckStatus.PASS
    combined = json.dumps(result.details) + result.message
    assert "secret" not in combined
    assert "token" not in combined


def test_postgres_endpoint_missing_at_host_does_not_leak(pf: ModuleType) -> None:
    url = "postgresql://user:my_secret_pass/db"
    result = pf.check_postgres_endpoint(url, 1.0)
    assert result.status is pf.CheckStatus.FAIL
    combined = json.dumps(result.details) + result.message
    assert "my_secret_pass" not in combined
    assert "does not parse" in result.message.lower()


def test_postgres_endpoint_numeric_password_does_not_leak(pf: ModuleType) -> None:
    url = "postgresql://user:999999/db"
    result = pf.check_postgres_endpoint(url, 1.0)
    assert result.status is pf.CheckStatus.FAIL
    combined = json.dumps(result.details) + result.message
    assert "999999" not in combined
    assert "does not parse" in result.message.lower()


def test_postgres_endpoint_adversarial_redaction_comprehensive(pf: ModuleType) -> None:
    # 1. Parsing with question mark in password extracts correct host/port
    host, port = pf._parse_postgres_endpoint("postgresql://user:secret?token@localhost:5432/db")
    assert host == "localhost"
    assert port == 5432

    # 2. Assert zero leaks in CheckResult across adversarial inputs
    cases: list[tuple[str, list[str]]] = [
        ("postgresql://user:secret?token@localhost:5432/db", ["secret", "token"]),
        ("postgresql://user:my_secret_pass/db", ["my_secret_pass"]),
        ("postgresql://user:999999/db", ["999999"]),
        ("postgresql://user:0/db", ["user"]),
    ]
    for url, sensitive_tokens in cases:
        res = pf.check_postgres_endpoint(url, 0.1)
        for token in sensitive_tokens:
            assert token not in res.message, f"Leaked token {token!r} in {res.message}"
            assert token not in json.dumps(res.details)


def test_endpoint_refused_fails_with_proxy_hint(pf: ModuleType, closed_port: int) -> None:
    url = f"postgresql://user:{_SECRET_PASSWORD}@127.0.0.1:{closed_port}/db"
    result = pf.check_postgres_endpoint(url, 1.0)
    assert result.status is pf.CheckStatus.FAIL
    assert "cloud-sql-proxy" in result.remediation
    assert _SECRET_PASSWORD not in json.dumps(result.details) + result.message


@pytest.mark.parametrize(
    ("url", "fragment"),
    [
        ("", "no host"),
        ("   ", "no host"),
        ("invalid-url-no-scheme", "does not parse"),
        ("postgresql:///db-without-host", "no host"),
        ("postgresql://u@:5432/db", "no host"),
        ("postgresql://u@[::1/db", "does not parse"),
        ("postgresql://u@[::1]junk/db", "does not parse"),
        ("postgresql://u@127.0.0.1:99999/db", "does not parse"),
        ("postgresql://u@127.0.0.1:0/db", "invalid port 0"),
    ],
)
def test_endpoint_rejects_malformed_urls(pf: ModuleType, url: str, fragment: str) -> None:
    result = pf.check_postgres_endpoint(url, 1.0)
    assert result.status is pf.CheckStatus.FAIL
    assert fragment in result.message.lower()


def test_endpoint_ipv6_valid_formats(pf: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, int]] = []

    def fake_connect(addr: tuple[str, int], timeout: float) -> socket.socket:
        assert timeout > 0
        seen.append(addr)
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(pf.socket, "create_connection", fake_connect)
    pf.check_postgres_endpoint("postgresql://u@[::1]:5433/db", 1.0)
    pf.check_postgres_endpoint("postgresql://u@[::1]/db", 1.0)
    assert seen == [("::1", 5433), ("::1", pf.LIBPQ_DEFAULT_PORT)]


def test_endpoint_defaults_to_libpq_port(pf: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, int]] = []

    def fake_connect(addr: tuple[str, int], timeout: float) -> socket.socket:
        assert timeout > 0
        seen.append(addr)
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(pf.socket, "create_connection", fake_connect)
    pf.check_postgres_endpoint("postgresql://u@db.internal/db", 1.0)
    assert seen == [("db.internal", pf.LIBPQ_DEFAULT_PORT)]


# ── Orchestration + CLI ───────────────────────────────────────────────────────


def test_run_checks_healthy_gcp_config(pf: ModuleType, listening_port: int) -> None:
    settings = _gcp_settings(
        db=DBSettings(provider="postgres", url=f"postgresql://u@127.0.0.1:{listening_port}/db")
    )
    session = _healthy_session(tuple(pf.required_apis(settings)))
    results = pf.run_checks(settings, timeout=1.0, auth=_backend(pf, session=session))
    assert [r.name for r in results] == [
        "adc_credentials",
        "gcp_project",
        "billing",
        "required_apis",
        "postgres_endpoint",
    ]
    assert {r.status for r in results} == {pf.CheckStatus.PASS}


def test_run_checks_without_google_auth_degrades(pf: ModuleType) -> None:
    results = {r.name: r.status for r in pf.run_checks(_settings(), timeout=1.0, auth=None)}
    assert results["adc_credentials"] is pf.CheckStatus.FAIL
    assert results["billing"] is pf.CheckStatus.SKIP
    assert results["postgres_endpoint"] is pf.CheckStatus.SKIP  # sqlite default


def test_explicit_db_url_overrides_sqlite_provider(pf: ModuleType, closed_port: int) -> None:
    results = pf.run_checks(
        _settings(), db_url=f"postgresql://u@127.0.0.1:{closed_port}/db", timeout=1.0, auth=None
    )
    assert results[-1].status is pf.CheckStatus.FAIL


def test_main_json_report_shape_and_exit_codes(
    pf: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    session = _healthy_session(("aiplatform.googleapis.com",))
    settings = _settings(llm=LLMSettings(provider="vertex", project_id=_PROJECT))
    code = pf.main(["--json"], settings=settings, auth_loader=lambda: _backend(pf, session=session))
    payload = json.loads(capsys.readouterr().out)
    assert code == pf.EXIT_OK
    assert payload["all_passed"] is True
    assert set(payload) == {"all_passed", "summary", "checks"}
    assert set(payload["checks"][0]) == {"name", "status", "message", "details", "remediation"}
    assert payload["summary"]["total"] == len(payload["checks"])

    code = pf.main([], settings=_settings(), auth_loader=lambda: None)
    out = capsys.readouterr().out
    assert code == pf.EXIT_FAILURE
    assert "Remediation:" in out
    assert "Pre-flight FAILED" in out


@pytest.mark.parametrize("bad", ["0", "-1", "nan", "inf", "abc"])
def test_main_rejects_non_positive_or_non_finite_timeout(pf: ModuleType, bad: str) -> None:
    with pytest.raises(SystemExit) as exc:
        pf.main(["--timeout", bad], settings=_settings(), auth_loader=lambda: None)
    assert exc.value.code == 2


def test_timeout_default_comes_from_settings(pf: ModuleType) -> None:
    settings = _settings(db=DBSettings(connect_timeout_seconds=7.5))
    assert pf.build_parser(settings).parse_args([]).timeout == 7.5


def test_verbose_emits_debug_logs_without_tokens(
    pf: ModuleType, caplog: pytest.LogCaptureFixture
) -> None:
    session = _healthy_session(("aiplatform.googleapis.com",))
    settings = _settings(llm=LLMSettings(provider="vertex", project_id=_PROJECT))
    with caplog.at_level(logging.DEBUG, logger="verify_gcp_prerequisites"):
        pf.main(["-v"], settings=settings, auth_loader=lambda: _backend(pf, session=session))
    text = caplog.text
    assert "check=billing status=PASS" in text
    assert "billingInfo" in text  # the probed URL is logged at DEBUG
    assert "token" not in text.lower()


def test_load_google_auth_returns_none_when_missing(
    pf: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys  # noqa: PLC0415

    monkeypatch.setitem(sys.modules, "google.auth", None)
    assert pf.load_google_auth() is None


def test_load_google_auth_wires_the_sdk_names(
    pf: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Success path, proven with stand-in modules so `[dev]`-only CI covers it."""
    import sys  # noqa: PLC0415

    google, auth, exceptions, transport, requests = (
        ModuleType(n)
        for n in (
            "google",
            "google.auth",
            "google.auth.exceptions",
            "google.auth.transport",
            "google.auth.transport.requests",
        )
    )

    def default(**_kw: Any) -> tuple[None, None]:
        return None, None

    auth.default = default  # type: ignore[attr-defined]
    exceptions.DefaultCredentialsError = _CredError  # type: ignore[attr-defined]
    exceptions.RefreshError = _RefreshError  # type: ignore[attr-defined]
    requests.AuthorizedSession = _Session  # type: ignore[attr-defined]
    requests.Request = object  # type: ignore[attr-defined]
    google.auth = auth  # type: ignore[attr-defined]
    auth.exceptions = exceptions  # type: ignore[attr-defined]
    auth.transport = transport  # type: ignore[attr-defined]
    transport.requests = requests  # type: ignore[attr-defined]
    for module in (google, auth, exceptions, transport, requests):
        monkeypatch.setitem(sys.modules, module.__name__, module)

    backend = pf.load_google_auth()
    assert backend is not None
    assert backend.default is default
    assert backend.credentials_error is _CredError
    assert backend.refresh_error is _RefreshError
    assert backend.make_session is _Session
