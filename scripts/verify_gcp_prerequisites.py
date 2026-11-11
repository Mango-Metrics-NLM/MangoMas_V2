#!/usr/bin/env python3
"""Pre-flight verification for the Mango-Mas GCP local CLI demo (spec-0049).

Answers one question before an operator runs the runbook: will the live
journeys fail for an environmental reason? Each check is read-only.

1. ``adc_credentials``   Application Default Credentials exist and refresh.
2. ``gcp_project``       A target project resolves (flag > Settings > ADC).
3. ``billing``           The project has billing enabled AND its billing
                         account is open. A project can report
                         ``billingEnabled=true`` while its account is closed,
                         and Vertex then rejects every call with
                         ``PERMISSION_DENIED``. That is the failure this check
                         was added for.
4. ``required_apis``     The APIs implied by the configured providers are
                         enabled (``aiplatform`` for a Vertex LLM/embeddings,
                         ``secretmanager`` for GCP secrets, ``cloudtrace``
                         for the GCP exporter), plus any ``--require-api``.
5. ``postgres_endpoint`` The host/port parsed from ``MANGOMAS_DB__URL``
                         accepts a TCP connection. This proves *something*
                         listens there (Cloud SQL Auth Proxy or a local
                         Postgres), not which.

Defaults come from ``mangomas.config.Settings`` (the same ``MANGOMAS_*``
variables the CLI reads), so the script and the app cannot disagree.
``google-auth`` is optional (``mangomas[vertex]``/``[gcp]``): without it the
cloud checks report SKIP/FAIL with an install hint instead of crashing.

Exit codes: 0 every check passed or skipped; 1 at least one check failed;
2 invalid arguments (argparse).
"""

from __future__ import annotations

import argparse
import enum
import json
import logging
import math
import os
import re
import shlex
import socket
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import quote, quote_plus

from mangomas.config import Settings

logger = logging.getLogger("verify_gcp_prerequisites")

EXIT_OK: Final[int] = 0
EXIT_FAILURE: Final[int] = 1

MIN_TCP_PORT: Final[int] = 1
MAX_TCP_PORT: Final[int] = 65535
# libpq's documented default; used only when MANGOMAS_DB__URL names no port.
LIBPQ_DEFAULT_PORT: Final[int] = 5432
HTTP_BAD_REQUEST: Final[int] = 400
HTTP_FORBIDDEN: Final[int] = 403

BILLING_ACCOUNT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^billingAccounts/[0-9A-F]{6}-[0-9A-F]{6}-[0-9A-F]{6}$"
)
GCP_PROJECT_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9-]{3,28}[a-z0-9]$")
GCP_API_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"
)

CLOUD_PLATFORM_SCOPE: Final[str] = "https://www.googleapis.com/auth/cloud-platform"
CLOUD_BILLING_API: Final[str] = "https://cloudbilling.googleapis.com/v1"
SERVICE_USAGE_API: Final[str] = "https://serviceusage.googleapis.com/v1"
INSTALL_HINT: Final[str] = "pip install 'mangomas[vertex]'  (or '[gcp]' / '[cloud]')"
ADC_LOGIN_HINT: Final[str] = "gcloud auth application-default login"

# Provider value -> the API a live call through it needs. Keys mirror the
# Literal values in mangomas.config; a provider absent here needs no API.
LLM_PROVIDER_APIS: Final[dict[str, str]] = {"vertex": "aiplatform.googleapis.com"}
SECRETS_PROVIDER_APIS: Final[dict[str, str]] = {"gcp": "secretmanager.googleapis.com"}
TELEMETRY_EXPORTER_APIS: Final[dict[str, str]] = {"gcp": "cloudtrace.googleapis.com"}
# Environment fallbacks the Google SDKs themselves honour, after Settings.
PROJECT_ENV_FALLBACKS: Final[tuple[str, ...]] = ("GOOGLE_CLOUD_PROJECT", "CLOUDSDK_CORE_PROJECT")


class CheckStatus(enum.StrEnum):
    PASS = "PASS"  # noqa: S105 -- status token, not a secret
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    remediation: str | None = None


@dataclass(frozen=True)
class GoogleAuthBackend:
    """The slice of ``google-auth`` this script uses; injectable for tests."""

    default: Callable[..., tuple[Any, str | None]]
    make_request: Callable[[], Any]
    make_session: Callable[[Any], Any]
    credentials_error: type[Exception]
    refresh_error: type[Exception]


@dataclass(frozen=True)
class AdcOutcome:
    result: CheckResult
    credentials: Any = None
    adc_project: str | None = None


def load_google_auth() -> GoogleAuthBackend | None:
    """Import google-auth lazily; ``None`` when the optional extra is absent."""
    try:
        import google.auth  # noqa: PLC0415
        import google.auth.exceptions  # noqa: PLC0415
        from google.auth.transport.requests import (  # noqa: PLC0415
            AuthorizedSession,
            Request,
        )
    except ImportError:
        logger.debug("google-auth not importable; cloud checks degrade")
        return None
    return GoogleAuthBackend(
        default=google.auth.default,
        make_request=Request,
        make_session=AuthorizedSession,
        credentials_error=google.auth.exceptions.DefaultCredentialsError,
        refresh_error=google.auth.exceptions.RefreshError,
    )


def _clean(value: str | None) -> str | None:
    """Strip whitespace; treat empty as unset."""
    cleaned = value.strip() if value else ""
    return cleaned or None


def check_adc_credentials(auth: GoogleAuthBackend | None) -> AdcOutcome:
    name = "adc_credentials"
    if auth is None:
        return AdcOutcome(
            CheckResult(
                name, CheckStatus.FAIL, "google-auth is not installed.", remediation=INSTALL_HINT
            )
        )
    try:
        credentials, adc_project = auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
    except auth.credentials_error as exc:
        return AdcOutcome(
            CheckResult(
                name,
                CheckStatus.FAIL,
                "Application Default Credentials (ADC) not found.",
                details={"error": str(exc)},
                remediation=f"Run: {ADC_LOGIN_HINT}",
            )
        )
    except Exception as exc:
        return AdcOutcome(
            CheckResult(
                name,
                CheckStatus.FAIL,
                f"Error discovering ADC: {exc}",
                details={"error": str(exc)},
                remediation=f"Check credential file permissions, or run: {ADC_LOGIN_HINT}",
            )
        )
    try:
        credentials.refresh(auth.make_request())
    except auth.refresh_error as exc:
        return AdcOutcome(
            CheckResult(
                name,
                CheckStatus.FAIL,
                "ADC token is expired or revoked.",
                details={"error": str(exc)},
                remediation=f"Run: {ADC_LOGIN_HINT}",
            )
        )
    except Exception as exc:
        return AdcOutcome(
            CheckResult(
                name,
                CheckStatus.FAIL,
                f"Network error refreshing the ADC token: {exc}",
                details={"error": str(exc)},
                remediation="Check connectivity to oauth2.googleapis.com.",
            )
        )
    # An identity label only; token values are never read into a result.
    account = (
        getattr(credentials, "service_account_email", None)
        or getattr(credentials, "client_id", None)
        or type(credentials).__name__
    )
    return AdcOutcome(
        CheckResult(
            name, CheckStatus.PASS, f"Valid ADC found ({account}).", details={"account": account}
        ),
        credentials=credentials,
        adc_project=_clean(adc_project),
    )


def resolve_project(
    settings: Settings, cli_project: str | None, adc_project: str | None
) -> str | None:
    """Flag > Settings (llm, embeddings, secrets) > SDK env vars > ADC default."""
    candidates = [
        cli_project,
        settings.llm.project_id,
        settings.embeddings.project_id,
        settings.secrets.project_id,
        *(os.environ.get(var) for var in PROJECT_ENV_FALLBACKS),
        adc_project,
    ]
    return next((p for p in map(_clean, candidates) if p), None)


def check_gcp_project(project: str | None) -> CheckResult:
    if not project:
        return CheckResult(
            "gcp_project",
            CheckStatus.FAIL,
            "No GCP project configured.",
            remediation=(
                "Pass --project, set MANGOMAS_LLM__PROJECT_ID, "
                "or run: gcloud config set project <ID>"
            ),
        )
    if not GCP_PROJECT_ID_PATTERN.match(project):
        return CheckResult(
            "gcp_project",
            CheckStatus.FAIL,
            (
                f"Invalid GCP project ID: {project!r} "
                "(must be 5-30 chars, lowercase alphanumeric/hyphen)."
            ),
            details={"project_id": project},
            remediation="Specify a valid GCP project ID: gcloud config set project <ID>",
        )
    return CheckResult(
        "gcp_project",
        CheckStatus.PASS,
        f"GCP project: {project}",
        details={"project_id": project},
    )


def required_apis(settings: Settings, extra: Sequence[str] = ()) -> list[str]:
    """APIs the configured providers need, plus explicit extras; sorted, unique."""
    apis = {
        LLM_PROVIDER_APIS.get(settings.llm.provider),
        SECRETS_PROVIDER_APIS.get(settings.secrets.provider),
        TELEMETRY_EXPORTER_APIS.get(settings.telemetry.exporter),
        *(map(_clean, extra)),
    }
    if settings.embeddings.enabled:
        apis.add(LLM_PROVIDER_APIS.get(settings.embeddings.provider))
    return sorted(a for a in apis if a)


def _get_json(session: Any, url: str, timeout: float) -> tuple[int, dict[str, Any]]:
    """GET ``url``; return (status, JSON object body or ``{}`` if not an object)."""
    response = session.get(url, timeout=timeout)
    status = int(response.status_code)
    try:
        body = response.json()
    except ValueError:  # error pages are not always JSON
        body = {}
    logger.debug("GET %s -> %s", url, status)
    return status, body if isinstance(body, dict) else {}


def _prereq_skip(name: str, session: Any, project: str | None) -> CheckResult | None:
    if session is None or project is None:
        return CheckResult(
            name, CheckStatus.SKIP, "Skipped: needs valid ADC and a resolved project."
        )
    return None


def _billing_project_verdict(
    status: int, info: dict[str, Any], project: str, console: str
) -> CheckResult | None:
    """Verdict from the project's billingInfo; ``None`` means 'go on to the account'."""
    if status == HTTP_FORBIDDEN:
        return CheckResult(
            "billing",
            CheckStatus.SKIP,
            "Cannot read billing info (403); billing state unverified.",
            remediation="Grant roles/billing.viewer, or verify billing in the console.",
        )
    if status >= HTTP_BAD_REQUEST:
        return CheckResult(
            "billing",
            CheckStatus.FAIL,
            f"Billing API returned HTTP {status}.",
            details={"http_status": status},
        )
    if not info.get("billingEnabled", False):
        return CheckResult(
            "billing",
            CheckStatus.FAIL,
            f"Billing is disabled for {project}.",
            remediation=f"Link a billing account: {console}",
        )
    return None


def _billing_account_verdict(
    status: int, account_info: dict[str, Any], account: str, console: str
) -> CheckResult:
    """Verdict from the billing account itself: the closed-account trap lives here."""
    details: dict[str, Any] = {"billing_account": account}
    if status == HTTP_FORBIDDEN:
        return CheckResult(
            "billing",
            CheckStatus.SKIP,
            "Billing enabled; account open/closed state not readable (403).",
            details=details,
            remediation=(
                "Grant roles/billing.viewer, or verify billing in the Google Cloud Console."
            ),
        )
    if status >= HTTP_BAD_REQUEST:
        return CheckResult(
            "billing",
            CheckStatus.FAIL,
            f"Billing account API returned HTTP {status}.",
            details={"billing_account": account, "http_status": status},
            remediation=f"Check account permissions or link an open account: {console}",
        )
    if account_info.get("open") is not True:
        return CheckResult(
            "billing",
            CheckStatus.FAIL,
            f"Billing account {account} is CLOSED; Vertex calls will fail with PERMISSION_DENIED.",
            details=details,
            remediation=(
                "Reopen the billing account (Billing > Account management), "
                f"or link an open one: {console}"
            ),
        )
    return CheckResult(
        "billing",
        CheckStatus.PASS,
        f"Billing enabled on an open account ({account}).",
        details=details,
    )


def check_billing(session: Any, project: str | None, timeout: float) -> CheckResult:
    if (skip := _prereq_skip("billing", session, project)) is not None:
        return skip
    escaped_project = quote(str(project), safe="")
    console = (
        f"https://console.cloud.google.com/billing/linkedaccount?project={quote_plus(str(project))}"
    )
    try:
        status, info = _get_json(
            session, f"{CLOUD_BILLING_API}/projects/{escaped_project}/billingInfo", timeout
        )
        verdict = _billing_project_verdict(status, info, str(project), console)
        if verdict is not None:
            return verdict
        account = str(info.get("billingAccountName", "")).strip()
        if not BILLING_ACCOUNT_PATTERN.match(account):
            return CheckResult(
                "billing",
                CheckStatus.FAIL,
                f"Malformed or missing billing account name: {account!r}",
                details={"billing_account": account},
                remediation=f"Link an open billing account: {console}",
            )
        escaped_account = quote(account, safe="/")
        acct_status, acct = _get_json(session, f"{CLOUD_BILLING_API}/{escaped_account}", timeout)
    except Exception as exc:
        return CheckResult(
            "billing",
            CheckStatus.FAIL,
            f"Billing probe failed: {exc}",
            details={"error": str(exc)},
        )
    return _billing_account_verdict(acct_status, acct, account, console)


def _validate_api_names(name: str, apis: Sequence[str]) -> CheckResult | None:
    for api in apis:
        if not GCP_API_NAME_PATTERN.match(api):
            return CheckResult(
                name,
                CheckStatus.FAIL,
                f"Invalid API service name: {api!r}",
                details={"api": api},
                remediation=(
                    "Ensure API names match standard GCP domain format "
                    "(e.g. aiplatform.googleapis.com)."
                ),
            )
    return None


def check_required_apis(
    session: Any, project: str | None, apis: Sequence[str], timeout: float
) -> CheckResult:
    name = "required_apis"
    if (skip := _prereq_skip(name, session, project)) is not None:
        return skip
    if not apis:
        return CheckResult(name, CheckStatus.SKIP, "No GCP provider configured; no API required.")

    # S3: Validate all API names
    if (invalid := _validate_api_names(name, apis)) is not None:
        return invalid

    disabled: list[str] = []
    probe_errors: list[dict[str, Any]] = []
    forbidden_apis: list[str] = []

    escaped_project = quote(str(project), safe="")

    # Y2: Evaluate all APIs without premature exit
    for api in apis:
        escaped_api = quote(api, safe="")
        try:
            url = f"{SERVICE_USAGE_API}/projects/{escaped_project}/services/{escaped_api}"
            status, body = _get_json(session, url, timeout)
            if status == HTTP_FORBIDDEN:
                forbidden_apis.append(api)
            elif status >= HTTP_BAD_REQUEST:
                probe_errors.append({"api": api, "http_status": status})
            elif not isinstance(body, dict) or body.get("state") != "ENABLED":
                disabled.append(api)
        except Exception as exc:
            probe_errors.append({"api": api, "error": str(exc)})

    if probe_errors:
        return CheckResult(
            name,
            CheckStatus.FAIL,
            f"Service Usage probe failed for {len(probe_errors)} API(s).",
            details={"apis": list(apis), "probe_errors": probe_errors},
            remediation="Check network connectivity and permissions for Service Usage API.",
        )

    if disabled:
        quoted_disabled = " ".join(shlex.quote(a) for a in disabled)
        quoted_project = shlex.quote(str(project))
        return CheckResult(
            name,
            CheckStatus.FAIL,
            f"Disabled API(s): {', '.join(disabled)}",
            details={"apis": list(apis), "disabled": disabled},
            remediation=f"gcloud services enable {quoted_disabled} --project {quoted_project}",
        )

    if forbidden_apis:
        verdict_status = CheckStatus.SKIP
        msg = (
            f"Cannot read service state (403); {len(forbidden_apis)} API(s) unverified: "
            f"{', '.join(forbidden_apis)}."
        )
        details: dict[str, Any] = {"apis": list(apis), "forbidden": forbidden_apis}
        remediation: str | None = (
            "Grant roles/serviceusage.serviceUsageConsumer or verify API enablement in the console."
        )
    else:
        verdict_status = CheckStatus.PASS
        msg = f"All {len(apis)} required API(s) enabled."
        details = {"apis": list(apis)}
        remediation = None

    return CheckResult(name, verdict_status, msg, details=details, remediation=remediation)


def _parse_host_and_port(host_port: str) -> tuple[str, str | None]:
    """Isolate host and raw port string from authority host_port."""
    if host_port.startswith("["):
        closing = host_port.find("]")
        if closing == -1:
            raise ValueError("MANGOMAS_DB__URL does not parse: unterminated IPv6 literal.")
        host = host_port[1:closing]
        rest = host_port[closing + 1 :]
        if rest.startswith(":"):
            return host, rest[1:]
        if not rest:
            return host, None
        raise ValueError("MANGOMAS_DB__URL does not parse: invalid host format.")

    if ":" in host_port:
        host, _, raw_port = host_port.rpartition(":")
        return host, raw_port
    return host_port, None


def _parse_postgres_endpoint(db_url: str) -> tuple[str, int]:
    """Safely extract (host, port) from a database URL without leaking credentials.

    Handles passwords containing '#', '?', '/', IPv6 literals, and omitted ports.
    Raises ValueError with a sanitized message.
    """
    if not db_url or not db_url.strip():
        raise ValueError("MANGOMAS_DB__URL names no host.")
    url = db_url.strip()
    if "://" not in url:
        raise ValueError("MANGOMAS_DB__URL does not parse: missing scheme.")
    scheme, _, after_scheme = url.partition("://")
    if scheme.lower() not in ("postgres", "postgresql", "postgresql+asyncpg"):
        raise ValueError("MANGOMAS_DB__URL does not parse: unsupported scheme.")

    if "@" in after_scheme:
        last_at = after_scheme.rfind("@")
        after_at = after_scheme[last_at + 1 :]
        sep_indices = [i for i in (after_at.find("/"), after_at.find("?")) if i != -1]
        path_sep = min(sep_indices) if sep_indices else len(after_at)
        host_port = after_at[:path_sep].split("#", 1)[0]
    else:
        sep_indices = [i for i in (after_scheme.find("/"), after_scheme.find("?")) if i != -1]
        path_sep = min(sep_indices) if sep_indices else len(after_scheme)
        host_port = after_scheme[:path_sep].split("#", 1)[0]

    if not host_port:
        raise ValueError("MANGOMAS_DB__URL names no host.")

    host, raw_port = _parse_host_and_port(host_port)
    if not host:
        raise ValueError("MANGOMAS_DB__URL names no host.")

    if raw_port is None:
        port = LIBPQ_DEFAULT_PORT
    else:
        try:
            port = int(raw_port)
        except ValueError:
            raise ValueError("MANGOMAS_DB__URL does not parse: invalid port.") from None

    if not (MIN_TCP_PORT <= port <= MAX_TCP_PORT):
        if port == 0:
            raise ValueError("Invalid port 0 (1-65535).")
        raise ValueError("MANGOMAS_DB__URL does not parse: port out of range (1-65535).")

    return host, port


_POSTGRES_SANITY_MESSAGES: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("names no host", "MANGOMAS_DB__URL names no host.", {"host": None, "port": None}),
    ("invalid port 0", "Invalid port 0 (1-65535).", {"host": None, "port": 0}),
    (
        "unterminated ipv6",
        "MANGOMAS_DB__URL does not parse: unterminated IPv6 literal.",
        {"host": None, "port": None},
    ),
    (
        "invalid host format",
        "MANGOMAS_DB__URL does not parse: invalid host format.",
        {"host": None, "port": None},
    ),
    (
        "port out of range",
        "MANGOMAS_DB__URL does not parse: port out of range (1-65535).",
        {"host": None, "port": None},
    ),
    (
        "invalid port",
        "MANGOMAS_DB__URL does not parse: invalid port.",
        {"host": None, "port": None},
    ),
    (
        "missing scheme",
        "MANGOMAS_DB__URL does not parse: missing scheme.",
        {"host": None, "port": None},
    ),
    (
        "unsupported scheme",
        "MANGOMAS_DB__URL does not parse: unsupported scheme.",
        {"host": None, "port": None},
    ),
)


def check_postgres_endpoint(db_url: str | None, timeout: float) -> CheckResult:
    """TCP-probe the host/port of a ``postgres*://`` URL.

    The URL itself is never echoed: it may carry a password in its userinfo.
    """
    name = "postgres_endpoint"
    if db_url is None:
        return CheckResult(name, CheckStatus.SKIP, "Skipped: storage provider is not postgres.")

    try:
        host, port = _parse_postgres_endpoint(db_url)
    except ValueError as exc:
        msg_lower = str(exc).lower()
        fail_msg = "MANGOMAS_DB__URL does not parse."
        fail_details: dict[str, Any] = {"host": None, "port": None}
        for needle, clean_msg, sanity_details in _POSTGRES_SANITY_MESSAGES:
            if needle in msg_lower:
                fail_msg = clean_msg
                fail_details = sanity_details
                break
        return CheckResult(name, CheckStatus.FAIL, fail_msg, details=fail_details)

    details: dict[str, Any] = {"host": host, "port": port}
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except (OSError, ValueError, OverflowError) as exc:
        return CheckResult(
            name,
            CheckStatus.FAIL,
            f"Nothing accepts TCP on {host}:{port} ({exc}).",
            details={**details, "error": str(exc)},
            remediation=(
                f"Start the proxy: cloud-sql-proxy <PROJECT>:<REGION>:<INSTANCE> --port {port}\n"
                "Or use SQLite locally: MANGOMAS_DB__PROVIDER=sqlite"
            ),
        )
    return CheckResult(
        name,
        CheckStatus.PASS,
        f"TCP reachable at {host}:{port} (proxy or local Postgres).",
        details=details,
    )


def run_checks(
    settings: Settings,
    *,
    project: str | None = None,
    db_url: str | None = None,
    extra_apis: Sequence[str] = (),
    timeout: float,
    auth: GoogleAuthBackend | None = None,
) -> list[CheckResult]:
    adc = check_adc_credentials(auth)
    resolved = resolve_project(settings, project, adc.adc_project)
    session = auth.make_session(adc.credentials) if auth and adc.credentials is not None else None
    url = db_url or (settings.db.url if settings.db.provider == "postgres" else None)
    results = [
        adc.result,
        check_gcp_project(resolved),
        check_billing(session, resolved, timeout),
        check_required_apis(session, resolved, required_apis(settings, extra_apis), timeout),
        check_postgres_endpoint(url, timeout),
    ]
    for r in results:
        logger.info("check=%s status=%s", r.name, r.status.value)
    return results


def _counts(checks: Sequence[CheckResult]) -> dict[str, int]:
    return {
        "total": len(checks),
        "passed": sum(c.status is CheckStatus.PASS for c in checks),
        "failed": sum(c.status is CheckStatus.FAIL for c in checks),
        "skipped": sum(c.status is CheckStatus.SKIP for c in checks),
    }


def format_text_report(checks: Sequence[CheckResult], width: int = 78) -> str:
    lines = ["=" * width, "Mango-Mas GCP Demonstration Pre-flight Verification", "=" * width]
    for c in checks:
        lines.append(f"[{c.status.value}] {c.name}: {c.message}")
        if c.status is CheckStatus.FAIL and c.remediation:
            lines += ["  Remediation:", *(f"    {r}" for r in c.remediation.splitlines())]
    n = _counts(checks)
    verdict = "SUCCESSFUL" if n["failed"] == 0 else "FAILED"
    lines += [
        "-" * width,
        f"Result: {n['passed']} passed, {n['failed']} failed, {n['skipped']} skipped "
        f"of {n['total']}. Pre-flight {verdict}.",
        "=" * width,
    ]
    return "\n".join(lines)


def format_json_report(checks: Sequence[CheckResult]) -> str:
    payload = {
        "all_passed": all(c.status is not CheckStatus.FAIL for c in checks),
        "summary": _counts(checks),
        "checks": [
            {
                "name": c.name,
                "status": c.status.value,
                "message": c.message,
                "details": c.details,
                "remediation": c.remediation,
            }
            for c in checks
        ],
    }
    return json.dumps(payload, indent=2)


def _positive_finite(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a number: {raw!r}") from exc
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive finite number, got {raw!r}")
    return value


def build_parser(settings: Settings) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify workstation prerequisites for the GCP demo (spec-0049)."
    )
    parser.add_argument("--project", help="GCP project ID (default: Settings, SDK env, then ADC).")
    parser.add_argument(
        "--db-url", help="Postgres URL to probe (default: MANGOMAS_DB__URL when provider=postgres)."
    )
    parser.add_argument(
        "--require-api",
        action="append",
        default=[],
        metavar="SERVICE",
        help="Extra API that must be enabled, e.g. sqladmin.googleapis.com (repeatable).",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_finite,
        default=settings.db.connect_timeout_seconds,
        help="Per-probe timeout in seconds (default: MANGOMAS_DB__CONNECT_TIMEOUT_SECONDS).",
    )
    parser.add_argument("--json", action="store_true", help="Emit a JSON report on stdout.")
    parser.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging on stderr.")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    auth_loader: Callable[[], GoogleAuthBackend | None] = load_google_auth,
) -> int:
    settings = settings or Settings()
    args = build_parser(settings).parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )
    checks = run_checks(
        settings,
        project=args.project,
        db_url=args.db_url,
        extra_apis=args.require_api,
        timeout=args.timeout,
        auth=auth_loader(),
    )
    print(format_json_report(checks) if args.json else format_text_report(checks))
    return EXIT_OK if all(c.status is not CheckStatus.FAIL for c in checks) else EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
