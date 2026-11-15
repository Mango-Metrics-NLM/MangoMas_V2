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
                         enabled (``aiplatform`` for a Vertex LLM, a Vertex
                         ``MANGOMAS_LLM_PROFILES__*`` profile or Vertex
                         embeddings, ``secretmanager`` for GCP secrets,
                         ``cloudtrace`` for the GCP exporter), plus any
                         ``--require-api``. Each API is probed in the project
                         its runtime client actually targets, and billing is
                         checked for every such project.
5. ``postgres_endpoint`` The host/port parsed from ``MANGOMAS_DB__URL``
                         accepts a TCP connection. This proves *something*
                         listens there (Cloud SQL Auth Proxy or a local
                         Postgres), not which. Reserved characters in the
                         URL's userinfo must be percent-encoded, as libpq and
                         asyncpg require; an ambiguous URL fails without
                         echoing any part of it.

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
import functools
import inspect
import json
import logging
import math
import os
import re
import shlex
import socket
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import quote, quote_plus

import _gcp_postgres_probe
import _logging
import _stdio

from mangomas.config import Settings

logger = logging.getLogger("verify_gcp_prerequisites")

EXIT_OK: Final[int] = 0
EXIT_FAILURE: Final[int] = 1
EXIT_USAGE_ERROR: Final[int] = 2
DEFAULT_REPORT_WIDTH: Final[int] = 78

MIN_TCP_PORT: Final[int] = _gcp_postgres_probe.MIN_TCP_PORT
MAX_TCP_PORT: Final[int] = _gcp_postgres_probe.MAX_TCP_PORT
LIBPQ_DEFAULT_PORT: Final[int] = _gcp_postgres_probe.LIBPQ_DEFAULT_PORT
HTTP_BAD_REQUEST: Final[int] = 400
HTTP_FORBIDDEN: Final[int] = 403

BILLING_ACCOUNT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\AbillingAccounts/[0-9A-F]{6}-[0-9A-F]{6}-[0-9A-F]{6}\Z"
)
GCP_PROJECT_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"\A[a-z][a-z0-9-]{3,28}[a-z0-9]\Z")
GCP_API_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\A[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+\Z"
)
CROSS_PROJECT_RESOURCE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\Aprojects/([^/]+)/secrets/[^/]+(?:/versions/[^/]+)?\Z"
)

CLOUD_PLATFORM_SCOPE: Final[str] = "https://www.googleapis.com/auth/cloud-platform"
CLOUD_BILLING_API: Final[str] = "https://cloudbilling.googleapis.com/v1"
SERVICE_USAGE_API: Final[str] = "https://serviceusage.googleapis.com/v1"
INSTALL_HINT: Final[str] = "pip install 'mangomas[vertex]'  (or '[gcp]' / '[cloud]')"
ADC_LOGIN_HINT: Final[str] = "gcloud auth application-default login"

# Provider value -> the API a live call through it needs. Keys mirror the
# Literal values in mangomas.config; a provider absent here needs no API.
LLM_PROVIDER_APIS: Final[dict[str, str]] = {"vertex": "aiplatform.googleapis.com"}
EMBEDDINGS_PROVIDER_APIS: Final[dict[str, str]] = LLM_PROVIDER_APIS
SECRETS_PROVIDER_APIS: Final[dict[str, str]] = {"gcp": "secretmanager.googleapis.com"}
TELEMETRY_EXPORTER_APIS: Final[dict[str, str]] = {"gcp": "cloudtrace.googleapis.com"}
# Only consulted when MANGOMAS_TELEMETRY__METRICS_ENABLED=true (ADR-0013): the
# same exporter token then also builds a Cloud Monitoring metric exporter.
TELEMETRY_METRICS_APIS: Final[dict[str, str]] = {"gcp": "monitoring.googleapis.com"}
# google.auth.default() reads these before the credential's own project, so a
# project-less client (Cloud Trace exporter, Vertex without project_id) uses it.
ADC_PROJECT_ENV_OVERRIDES: Final[tuple[str, ...]] = ("GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT")
# Environment fallbacks for the *primary* project, after Settings.
PROJECT_ENV_FALLBACKS: Final[tuple[str, ...]] = (
    *ADC_PROJECT_ENV_OVERRIDES,
    "CLOUDSDK_CORE_PROJECT",
)
SECRETS_PROJECT_REQUIRED: Final[str] = (
    "MANGOMAS_SECRETS__PROJECT_ID is required when MANGOMAS_SECRETS__PROVIDER=gcp "
    "(the app refuses to start without it)."
)
INVALID_CROSS_PROJECT_REF: Final[str] = (
    "A cross-project projects/<id>/secrets/<name> reference has an invalid "
    "GCP project ID or resource path."
)

# Static probe labels: the only thing DEBUG logging records about a request.
# A URL embeds identifiers read from configuration, so it is never logged, and
# the labels avoid names a taint analyser classes as sensitive.
PROBE_CB_PROJECT: Final[str] = "cb_project"
PROBE_CB_ACCOUNT: Final[str] = "cb_account"
PROBE_SERVICE_USAGE: Final[str] = "service_usage"

POSTGRES_SCHEMES = _gcp_postgres_probe.POSTGRES_SCHEMES
URL_AUTHORITY_DELIMITERS = _gcp_postgres_probe.URL_AUTHORITY_DELIMITERS
POSTGRES_QUERY_ENDPOINT_OVERRIDES = _gcp_postgres_probe.POSTGRES_QUERY_ENDPOINT_OVERRIDES
POSTGRES_UNSUPPORTED_HOST_CHARS = _gcp_postgres_probe.POSTGRES_UNSUPPORTED_HOST_CHARS
REDACTED_PORT_PLACEHOLDER = _gcp_postgres_probe.REDACTED_PORT_PLACEHOLDER
PERCENT_ENCODING_HINT = _gcp_postgres_probe.PERCENT_ENCODING_HINT
UNSUPPORTED_ENDPOINT_HINT = _gcp_postgres_probe.UNSUPPORTED_ENDPOINT_HINT


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
    make_session: Callable[..., Any]
    credentials_error: type[Exception]
    refresh_error: type[Exception]


@dataclass(frozen=True)
class AdcOutcome:
    result: CheckResult
    credentials: Any = None
    adc_project: str | None = None


@dataclass(frozen=True)
class ApiRequirement:
    """One API a configured client needs, in the project that client targets."""

    api: str
    project: str | None
    source: str


@dataclass
class _ApiProbe:
    """Service Usage outcome for one project's APIs."""

    disabled: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)


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


def _accepts_kwarg(fn: Callable[..., Any], kwarg: str) -> bool:
    """True when ``fn`` accepts ``kwarg`` by name or ``**kwargs``."""
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        param.kind is inspect.Parameter.VAR_KEYWORD or param.name == kwarg for param in params
    )


def _open_session(auth: GoogleAuthBackend | None, credentials: Any, timeout: float) -> Any:
    """Build an authenticated HTTP session bounded by ``timeout`` when supported."""
    if auth is None or credentials is None:
        return None
    if _accepts_kwarg(auth.make_session, "refresh_timeout"):
        return auth.make_session(credentials, refresh_timeout=timeout)
    return auth.make_session(credentials)


def check_adc_credentials(
    auth: GoogleAuthBackend | None, timeout: float | None = None
) -> AdcOutcome:
    """Discover ADC and refresh the token once.

    ``timeout`` bounds the token refresh the same way google-auth bounds its
    own refreshes (``functools.partial(request, timeout=...)``); without it the
    transport's default (120 s for ``google.auth.transport.requests``) applies.
    Exception payloads are reduced to ``type(exc).__name__`` so proxy URLs,
    OAuth bodies, or credential-file snippets never reach stdout or JSON.
    """
    name = "adc_credentials"
    if auth is None:
        return AdcOutcome(
            CheckResult(
                name, CheckStatus.FAIL, "google-auth is not installed.", remediation=INSTALL_HINT
            )
        )
    request = auth.make_request()
    if timeout is not None:
        request = functools.partial(request, timeout=timeout)
    default_kwargs: dict[str, Any] = {"scopes": [CLOUD_PLATFORM_SCOPE]}
    if timeout is not None and _accepts_kwarg(auth.default, "request"):
        default_kwargs["request"] = request
    try:
        credentials, adc_project = auth.default(**default_kwargs)
    except auth.credentials_error as exc:
        err_type = type(exc).__name__
        logger.debug("adc discovery failed error=%s", err_type)
        return AdcOutcome(
            CheckResult(
                name,
                CheckStatus.FAIL,
                "Application Default Credentials (ADC) not found.",
                details={"error": err_type},
                remediation=f"Run: {ADC_LOGIN_HINT}",
            )
        )
    except Exception as exc:
        err_type = type(exc).__name__
        logger.debug("adc discovery unexpected error=%s", err_type)
        return AdcOutcome(
            CheckResult(
                name,
                CheckStatus.FAIL,
                f"Error discovering ADC ({err_type}).",
                details={"error": err_type},
                remediation=f"Check credential file permissions, or run: {ADC_LOGIN_HINT}",
            )
        )
    try:
        logger.debug("ADC refresh starting (timeout=%s)", timeout)
        credentials.refresh(request)
    except auth.refresh_error as exc:
        err_type = type(exc).__name__
        logger.debug("adc refresh rejected error=%s", err_type)
        return AdcOutcome(
            CheckResult(
                name,
                CheckStatus.FAIL,
                "ADC token is expired or revoked.",
                details={"error": err_type},
                remediation=f"Run: {ADC_LOGIN_HINT}",
            )
        )
    except Exception as exc:
        err_type = type(exc).__name__
        logger.debug("adc refresh transport error=%s", err_type)
        return AdcOutcome(
            CheckResult(
                name,
                CheckStatus.FAIL,
                f"Network error refreshing the ADC token ({err_type}).",
                details={"error": err_type},
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
    """Flag > Settings (llm, llm profiles, embeddings, secrets) > SDK env vars > ADC default."""
    candidates = [
        cli_project,
        settings.llm.project_id,
        *(profile.project_id for _, profile in sorted(settings.llm_profiles.items())),
        settings.embeddings.project_id,
        settings.secrets.project_id,
        *(os.environ.get(var) for var in PROJECT_ENV_FALLBACKS),
        adc_project,
    ]
    return next((p for p in map(_clean, candidates) if p), None)


def resolve_ambient_project(adc_project: str | None, fallback: str | None = None) -> str | None:
    """The project a client uses at runtime when its provider sets no ``project_id``.

    Mirrors ``google.auth.default()``, which the Cloud Trace exporter and a
    project-less Vertex client fall back to: ``GOOGLE_CLOUD_PROJECT``,
    ``GCLOUD_PROJECT``, then the credential's own project. ``--project`` is
    deliberately absent: nothing at runtime reads it. ``fallback`` (the primary
    project) applies only when none is known, so the check still has somewhere
    to probe.
    """
    candidates = [
        *(os.environ.get(var) for var in ADC_PROJECT_ENV_OVERRIDES),
        adc_project,
        fallback,
    ]
    return next((p for p in map(_clean, candidates) if p), None)


def _extract_resource_project(raw_ref: str | None) -> str | None:
    """Extract valid ``<project>`` from ``projects/<project>/secrets/<id>[/versions/<v>]``."""
    cleaned = _clean(raw_ref)
    if cleaned is None:
        return None
    match = CROSS_PROJECT_RESOURCE_PATTERN.match(cleaned)
    if match is None:
        return None
    candidate = match.group(1)
    return candidate if GCP_PROJECT_ID_PATTERN.match(candidate) else None


def _configured_resource_refs(settings: Settings) -> list[str | None]:
    """Configured resource-reference strings active at runtime."""
    refs: list[str | None] = [
        settings.llm.secret_ref,
        *(prof.secret_ref for _, prof in sorted(settings.llm_profiles.items())),
    ]
    if settings.auth.enabled:
        refs.append(settings.auth.secret_ref)
    return refs


def configuration_problems(settings: Settings) -> list[str]:
    """Static GCP misconfigurations the app itself refuses to start with."""
    problems: list[str] = []
    if settings.secrets.provider in SECRETS_PROVIDER_APIS:
        if not _clean(settings.secrets.project_id):
            problems.append(SECRETS_PROJECT_REQUIRED)
        for raw_ref in _configured_resource_refs(settings):
            cleaned = _clean(raw_ref)
            if (
                cleaned is not None
                and cleaned.startswith("projects/")
                and _extract_resource_project(cleaned) is None
            ):
                problems.append(INVALID_CROSS_PROJECT_REF)
                break
    return problems


def check_gcp_project(
    project: str | None,
    extra_projects: Sequence[str | None] = (),
    problems: Sequence[str] = (),
) -> CheckResult:
    """Validate the primary project, every other project a provider targets, and
    the static project configuration the app requires."""
    if problems:
        return CheckResult(
            "gcp_project",
            CheckStatus.FAIL,
            " ".join(problems),
            details={"project_id": project, "problems": list(problems)},
            remediation=(
                "Set a valid *_PROJECT_ID or cross-project resource path (see .env.gcp-demo)."
            ),
        )
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
    projects = list(dict.fromkeys([project, *(p for p in map(_clean, extra_projects) if p)]))
    invalid = [p for p in projects if not GCP_PROJECT_ID_PATTERN.match(p)]
    if invalid:
        return CheckResult(
            "gcp_project",
            CheckStatus.FAIL,
            (
                f"Invalid GCP project ID: {', '.join(repr(p) for p in invalid)} "
                "(must be 5-30 chars, lowercase alphanumeric/hyphen)."
            ),
            details={"project_id": project, "invalid": invalid},
            remediation="Specify a valid GCP project ID: gcloud config set project <ID>",
        )
    if len(projects) == 1:
        return CheckResult(
            "gcp_project",
            CheckStatus.PASS,
            f"GCP project: {project}",
            details={"project_id": project},
        )
    others = projects[1:]
    return CheckResult(
        "gcp_project",
        CheckStatus.PASS,
        f"GCP project: {project} (providers also target: {', '.join(others)})",
        details={"project_id": project, "projects": projects},
    )


def api_requirements(
    settings: Settings,
    extra: Sequence[str] = (),
    *,
    primary_project: str | None = None,
    ambient_project: str | None = None,
) -> list[ApiRequirement]:
    """Every API a configured client needs, paired with the project it targets.

    Project precedence mirrors the composition root: a provider's own
    ``project_id`` wins; a named LLM profile inherits ``MANGOMAS_LLM__PROJECT_ID``
    when unset (``composition.llm._merge_profile``); anything still unset uses
    ``ambient_project``. ``--require-api`` extras target ``primary_project``.
    """
    requirements: list[ApiRequirement] = []

    def add(api: str | None, explicit_project: str | None, source: str) -> None:
        if api:
            project = _clean(explicit_project) or ambient_project
            requirements.append(ApiRequirement(api=api, project=project, source=source))

    llm_project = _clean(settings.llm.project_id)
    add(LLM_PROVIDER_APIS.get(settings.llm.provider), llm_project, "llm")
    for profile_name, profile in sorted(settings.llm_profiles.items()):
        add(
            LLM_PROVIDER_APIS.get(profile.provider),
            _clean(profile.project_id) or llm_project,
            f"llm_profiles.{profile_name}",
        )
    if settings.embeddings.enabled:
        add(
            EMBEDDINGS_PROVIDER_APIS.get(settings.embeddings.provider),
            settings.embeddings.project_id,
            "embeddings",
        )
    secrets_api = SECRETS_PROVIDER_APIS.get(settings.secrets.provider)
    add(secrets_api, settings.secrets.project_id, "secrets")
    if secrets_api is not None:
        cross_project_refs: list[tuple[str, str | None]] = [
            ("llm_ref", settings.llm.secret_ref),
            *(
                ("llm_profile_ref", prof.secret_ref)
                for _, prof in sorted(settings.llm_profiles.items())
            ),
        ]
        if settings.auth.enabled:
            cross_project_refs.append(("auth_ref", settings.auth.secret_ref))
        for ref_source, ref_value in cross_project_refs:
            if (ref_project := _extract_resource_project(ref_value)) is not None:
                add(secrets_api, ref_project, ref_source)
    # The Cloud Trace exporter is built with no project: it uses the ADC default.
    add(TELEMETRY_EXPORTER_APIS.get(settings.telemetry.exporter), None, "telemetry")
    if settings.telemetry.metrics_enabled:
        add(TELEMETRY_METRICS_APIS.get(settings.telemetry.exporter), None, "telemetry.metrics")
    if settings.harness.enabled:
        add(
            TELEMETRY_EXPORTER_APIS.get(settings.harness.metrics_exporter),
            None,
            "harness.exporter",
        )
    for api in map(_clean, extra):
        if api:
            requirements.append(
                ApiRequirement(api=api, project=primary_project, source="--require-api")
            )
    return requirements


def api_plan(requirements: Sequence[ApiRequirement]) -> dict[str | None, list[str]]:
    """Group requirements by project: ``{project: sorted unique APIs}``, deterministic order."""
    grouped: dict[str | None, set[str]] = {}
    for req in requirements:
        grouped.setdefault(req.project, set()).add(req.api)
    ordered = sorted(grouped, key=lambda p: (p is None, p or ""))
    return {project: sorted(grouped[project]) for project in ordered}


def required_apis(settings: Settings, extra: Sequence[str] = ()) -> list[str]:
    """APIs the configured providers need, plus explicit extras; sorted, unique."""
    return sorted({req.api for req in api_requirements(settings, extra)})


def _get_json(
    session: Any, url: str, timeout: float, *, probe: str = PROBE_SERVICE_USAGE
) -> tuple[int, dict[str, Any]]:
    """GET ``url``; return (status, JSON object body or ``{}`` if not an object).

    Only the static ``probe`` label is logged, never ``url``: it embeds project
    and billing-account identifiers read from configuration.
    """
    response = session.get(url, timeout=timeout)
    status = int(response.status_code)
    try:
        body = response.json()
    except ValueError:  # error pages are not always JSON
        body = {}
    logger.debug("probe=%s status=%s", probe, status)
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
    project_id = str(project)
    if not GCP_PROJECT_ID_PATTERN.match(project_id):
        return CheckResult(
            "billing",
            CheckStatus.FAIL,
            f"Invalid GCP project ID: {project_id!r}",
            details={"project_id": project_id},
            remediation="Specify a valid GCP project ID: gcloud config set project <ID>",
        )
    escaped_project = quote(project_id, safe="")
    console = (
        f"https://console.cloud.google.com/billing/linkedaccount?project={quote_plus(project_id)}"
    )
    try:
        status, info = _get_json(
            session,
            f"{CLOUD_BILLING_API}/projects/{escaped_project}/billingInfo",
            timeout,
            probe=PROBE_CB_PROJECT,
        )
        verdict = _billing_project_verdict(status, info, project_id, console)
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
        acct_status, acct = _get_json(
            session, f"{CLOUD_BILLING_API}/{escaped_account}", timeout, probe=PROBE_CB_ACCOUNT
        )
    except Exception as exc:
        err_type = type(exc).__name__
        logger.debug("probe=billing transport_error=%s", err_type)
        return CheckResult(
            "billing",
            CheckStatus.FAIL,
            f"Billing probe failed ({err_type}).",
            details={"error": err_type},
        )
    return _billing_account_verdict(acct_status, acct, account, console)


_STATUS_SEVERITY: Final[dict[CheckStatus, int]] = {
    CheckStatus.PASS: 0,
    CheckStatus.SKIP: 1,
    CheckStatus.FAIL: 2,
}


def _merge_project_results(name: str, per_project: Mapping[str, CheckResult]) -> CheckResult:
    """Fold per-project results into one row; the worst status wins.

    A single project returns its result unchanged, so the one-project report
    (the common case) is identical to a direct call. No projects is a SKIP.
    """
    if not per_project:
        return CheckResult(name, CheckStatus.SKIP, "Skipped: no project to check.")
    if len(per_project) == 1:
        return next(iter(per_project.values()))
    worst = max((r.status for r in per_project.values()), key=_STATUS_SEVERITY.__getitem__)
    remediations = [
        f"[{project}] {line}"
        for project, result in per_project.items()
        if result.status is not CheckStatus.PASS and result.remediation
        for line in result.remediation.splitlines()
    ]
    return CheckResult(
        name,
        worst,
        "; ".join(f"[{project}] {result.message}" for project, result in per_project.items()),
        details={
            "projects": {
                project: {"status": result.status.value, **result.details}
                for project, result in per_project.items()
            }
        },
        remediation="\n".join(remediations) or None,
    )


def check_billing_projects(
    session: Any, projects: Sequence[str | None], timeout: float
) -> CheckResult:
    """Billing for every distinct project a provider targets.

    Billing on one project says nothing about another, so a Vertex client in
    project A and Secret Manager in project B are each checked. An invalid
    project ID is not probed (``gcp_project`` already fails it).
    """
    distinct = list(dict.fromkeys(projects)) or [None]
    resolved = [p for p in distinct if p is not None]
    if session is None or len(resolved) != len(distinct):
        return check_billing(session, None, timeout)
    per_project: dict[str, CheckResult] = {}
    for project in resolved:
        if not GCP_PROJECT_ID_PATTERN.match(project):
            per_project[project] = CheckResult(
                "billing", CheckStatus.SKIP, "Skipped: invalid project ID (see gcp_project)."
            )
            continue
        per_project[project] = check_billing(session, project, timeout)
    return _merge_project_results("billing", per_project)


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


def _probe_project_apis(
    session: Any, project: str, apis: Sequence[str], timeout: float
) -> _ApiProbe:
    """Probe every API in one project; never stops early (Y2)."""
    probe = _ApiProbe()
    escaped_project = quote(project, safe="")
    for api in apis:
        escaped_api = quote(api, safe="")
        try:
            url = f"{SERVICE_USAGE_API}/projects/{escaped_project}/services/{escaped_api}"
            status, body = _get_json(session, url, timeout, probe=PROBE_SERVICE_USAGE)
            if status == HTTP_FORBIDDEN:
                probe.forbidden.append(api)
            elif status >= HTTP_BAD_REQUEST:
                probe.errors.append({"api": api, "http_status": status})
            elif body.get("state") != "ENABLED":
                probe.disabled.append(api)
        except Exception as exc:
            err_type = type(exc).__name__
            logger.debug("probe=%s transport_error=%s", PROBE_SERVICE_USAGE, err_type)
            probe.errors.append({"api": api, "error": err_type})
    return probe


def _api_verdict(project: str, apis: Sequence[str], probe: _ApiProbe) -> CheckResult:
    """One project's verdict: probe errors > disabled > forbidden (SKIP) > PASS."""
    name = "required_apis"
    if probe.errors:
        return CheckResult(
            name,
            CheckStatus.FAIL,
            f"Service Usage probe failed for {len(probe.errors)} API(s).",
            details={"apis": list(apis), "probe_errors": probe.errors},
            remediation="Check network connectivity and permissions for Service Usage API.",
        )
    if probe.disabled:
        quoted_disabled = " ".join(shlex.quote(a) for a in probe.disabled)
        return CheckResult(
            name,
            CheckStatus.FAIL,
            f"Disabled API(s): {', '.join(probe.disabled)}",
            details={"apis": list(apis), "disabled": probe.disabled},
            remediation=(
                f"gcloud services enable {quoted_disabled} --project {shlex.quote(project)}"
            ),
        )
    if probe.forbidden:
        return CheckResult(
            name,
            CheckStatus.SKIP,
            (
                f"Cannot read service state (403); {len(probe.forbidden)} API(s) unverified: "
                f"{', '.join(probe.forbidden)}."
            ),
            details={"apis": list(apis), "forbidden": probe.forbidden},
            remediation=(
                "Grant roles/serviceusage.serviceUsageConsumer or verify API enablement "
                "in the console."
            ),
        )
    return CheckResult(
        name,
        CheckStatus.PASS,
        f"All {len(apis)} required API(s) enabled.",
        details={"apis": list(apis)},
    )


def check_required_apis_by_project(
    session: Any, plan: Mapping[str | None, Sequence[str]], timeout: float
) -> CheckResult:
    """Probe each project's required APIs and fold the verdicts into one row."""
    name = "required_apis"
    # Same precedence as the single-project check always had: missing ADC or an
    # unresolved project is reported before "nothing required".
    if session is None or any(project is None for project in plan):
        return CheckResult(
            name, CheckStatus.SKIP, "Skipped: needs valid ADC and a resolved project."
        )
    resolved = {
        project: list(dict.fromkeys(a for a in apis if a))
        for project, apis in plan.items()
        if project is not None
    }
    resolved = {project: apis for project, apis in resolved.items() if apis}
    if not resolved:
        return CheckResult(name, CheckStatus.SKIP, "No GCP provider configured; no API required.")
    # S3: Validate all API names before any request is built.
    all_apis = list(dict.fromkeys(a for apis in resolved.values() for a in apis))
    if (invalid := _validate_api_names(name, all_apis)) is not None:
        return invalid
    per_project: dict[str, CheckResult] = {}
    for project, apis in resolved.items():
        if not GCP_PROJECT_ID_PATTERN.match(project):
            per_project[project] = CheckResult(
                name, CheckStatus.SKIP, "Skipped: invalid project ID (see gcp_project)."
            )
            continue
        per_project[project] = _api_verdict(
            project, apis, _probe_project_apis(session, project, apis, timeout)
        )
    return _merge_project_results(name, per_project)


def check_required_apis(
    session: Any, project: str | None, apis: Sequence[str], timeout: float
) -> CheckResult:
    """Single-project form of :func:`check_required_apis_by_project`."""
    return check_required_apis_by_project(session, {project: list(apis)}, timeout)


_parse_bracketed_ipv6 = _gcp_postgres_probe.parse_bracketed_ipv6
_parse_host_and_port = _gcp_postgres_probe.parse_host_and_port
_postgres_authority = _gcp_postgres_probe.postgres_authority
_parse_postgres_endpoint = _gcp_postgres_probe.parse_postgres_endpoint
_PostgresParseFailure = _gcp_postgres_probe.PostgresParseFailure
_POSTGRES_PARSE_FAILURES = _gcp_postgres_probe.POSTGRES_PARSE_FAILURES
_REDACTED_ENDPOINT_LABEL = _gcp_postgres_probe.REDACTED_ENDPOINT_LABEL
_REDACTED_ENDPOINT_NOTE = _gcp_postgres_probe.REDACTED_ENDPOINT_NOTE


def _postgres_parse_failure(exc: ValueError) -> CheckResult:
    outcome = _gcp_postgres_probe.postgres_parse_failure_outcome(exc)
    return CheckResult(
        "postgres_endpoint",
        CheckStatus(outcome.status),
        outcome.message,
        details=outcome.details,
        remediation=outcome.remediation,
    )


def check_postgres_endpoint(db_url: str | None, timeout: float) -> CheckResult:
    """TCP-probe the host/port of a ``postgres*://`` URL without leaking credentials."""
    outcome = _gcp_postgres_probe.probe_postgres_endpoint(
        db_url,
        timeout,
        connect_fn=socket.create_connection,
        log=logger,
    )
    return CheckResult(
        "postgres_endpoint",
        CheckStatus(outcome.status),
        outcome.message,
        details=outcome.details,
        remediation=outcome.remediation,
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
    adc = check_adc_credentials(auth, timeout)
    resolved = resolve_project(settings, project, adc.adc_project)
    ambient = resolve_ambient_project(adc.adc_project, resolved)
    requirements = api_requirements(
        settings, extra_apis, primary_project=resolved, ambient_project=ambient
    )
    plan = api_plan(requirements)
    for req in requirements:
        logger.debug("requirement source=%s", req.source)
    session = _open_session(auth, adc.credentials, timeout)
    try:
        url = db_url or (settings.db.url if settings.db.provider == "postgres" else None)
        results = [
            adc.result,
            check_gcp_project(resolved, list(plan), configuration_problems(settings)),
            check_billing_projects(session, list(plan) or [resolved], timeout),
            # An empty plan still carries the primary project, so a missing project
            # is reported as such (the single-project precedence).
            check_required_apis_by_project(session, plan or {resolved: []}, timeout),
            check_postgres_endpoint(url, timeout),
        ]
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()
    for r in results:
        logger.debug("check=%s status=%s", r.name, r.status.value)
    return results


def _counts(checks: Sequence[CheckResult]) -> dict[str, int]:
    return {
        "total": len(checks),
        "passed": sum(c.status is CheckStatus.PASS for c in checks),
        "failed": sum(c.status is CheckStatus.FAIL for c in checks),
        "skipped": sum(c.status is CheckStatus.SKIP for c in checks),
    }


def format_text_report(checks: Sequence[CheckResult], width: int = DEFAULT_REPORT_WIDTH) -> str:
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
    parser.add_argument(
        "--project",
        help=(
            "Primary GCP project (default: Settings, SDK env, then ADC); --require-api "
            "APIs are checked here. A provider with its own *_PROJECT_ID is checked in "
            "that project; one without is checked where its client runs: "
            "GOOGLE_CLOUD_PROJECT / GCLOUD_PROJECT, then the ADC project."
        ),
    )
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


def _configure_logging(verbose: bool) -> None:
    """Set module logger level directly so ``-v`` works even when root handlers exist."""
    _logging.configure_script_logging(
        level=logging.DEBUG if verbose else logging.WARNING,
        module_logger=logger,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    auth_loader: Callable[[], GoogleAuthBackend | None] = load_google_auth,
) -> int:
    _stdio.configure_utf8_stdio()
    settings = settings or Settings()
    args = build_parser(settings).parse_args(argv)
    _configure_logging(args.verbose)
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
