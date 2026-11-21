"""Live read-only Google Cloud ADC, Billing, and Service Usage HTTP probes."""

from __future__ import annotations

import functools
import logging
import re
import shlex
from collections.abc import Mapping, Sequence
from typing import Any, Final
from urllib.parse import quote, quote_plus

from _gcp_preflight_plan import (
    ADC_LOGIN_HINT,
    CLOUD_BILLING_API,
    CLOUD_PLATFORM_SCOPE,
    GCP_PROJECT_ID_PATTERN,
    HTTP_BAD_REQUEST,
    HTTP_FORBIDDEN,
    INSTALL_HINT,
    PROBE_CB_ACCOUNT,
    PROBE_CB_PROJECT,
    PROBE_SERVICE_USAGE,
    SERVICE_USAGE_API,
    AdcOutcome,
    ApiProbe,
    CheckResult,
    CheckStatus,
    GoogleAuthBackend,
    accepts_kwarg,
    clean_str,
    merge_project_results,
)

BILLING_ACCOUNT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\AbillingAccounts/[0-9A-F]{6}-[0-9A-F]{6}-[0-9A-F]{6}\Z"
)
GCP_API_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\A[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+\Z"
)

logger = logging.getLogger("verify_gcp_prerequisites")


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


def open_session(auth: GoogleAuthBackend | None, credentials: Any, timeout: float) -> Any:
    """Build an authenticated HTTP session bounded by ``timeout`` when supported."""
    if auth is None or credentials is None:
        return None
    if accepts_kwarg(auth.make_session, "refresh_timeout"):
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
    if timeout is not None and accepts_kwarg(auth.default, "request"):
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
        adc_project=clean_str(adc_project),
    )


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
    """Verify billing enablement and open billing account state for one project."""
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
    return merge_project_results("billing", per_project)


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
) -> ApiProbe:
    """Probe every API in one project; never stops early (Y2)."""
    probe = ApiProbe()
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


def _api_verdict(project: str, apis: Sequence[str], probe: ApiProbe) -> CheckResult:
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
    return merge_project_results(name, per_project)


def check_required_apis(
    session: Any, project: str | None, apis: Sequence[str], timeout: float
) -> CheckResult:
    """Single-project form of :func:`check_required_apis_by_project`."""
    return check_required_apis_by_project(session, {project: list(apis)}, timeout)
