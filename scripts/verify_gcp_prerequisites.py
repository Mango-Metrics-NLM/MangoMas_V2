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
import json
import logging
import math
import socket
import sys
from collections.abc import Callable, Sequence
from typing import Any, Final

import _gcp_postgres_probe
import _gcp_preflight_plan
import _gcp_preflight_probes
import _logging
import _stdio

from mangomas.config import Settings

ADC_LOGIN_HINT = _gcp_preflight_plan.ADC_LOGIN_HINT
ADC_PROJECT_ENV_OVERRIDES = _gcp_preflight_plan.ADC_PROJECT_ENV_OVERRIDES
CLOUD_BILLING_API = _gcp_preflight_plan.CLOUD_BILLING_API
CLOUD_PLATFORM_SCOPE = _gcp_preflight_plan.CLOUD_PLATFORM_SCOPE
CROSS_PROJECT_RESOURCE_PATTERN = _gcp_preflight_plan.CROSS_PROJECT_RESOURCE_PATTERN
EMBEDDINGS_PROVIDER_APIS = _gcp_preflight_plan.EMBEDDINGS_PROVIDER_APIS
GCP_PROJECT_ID_PATTERN = _gcp_preflight_plan.GCP_PROJECT_ID_PATTERN
HTTP_BAD_REQUEST = _gcp_preflight_plan.HTTP_BAD_REQUEST
HTTP_FORBIDDEN = _gcp_preflight_plan.HTTP_FORBIDDEN
INSTALL_HINT = _gcp_preflight_plan.INSTALL_HINT
INVALID_CROSS_PROJECT_REF = _gcp_preflight_plan.INVALID_CROSS_PROJECT_REF
LLM_PROVIDER_APIS = _gcp_preflight_plan.LLM_PROVIDER_APIS
PROBE_CB_ACCOUNT = _gcp_preflight_plan.PROBE_CB_ACCOUNT
PROBE_CB_PROJECT = _gcp_preflight_plan.PROBE_CB_PROJECT
PROBE_SERVICE_USAGE = _gcp_preflight_plan.PROBE_SERVICE_USAGE
PROJECT_ENV_FALLBACKS = _gcp_preflight_plan.PROJECT_ENV_FALLBACKS
SECRETS_PROJECT_REQUIRED = _gcp_preflight_plan.SECRETS_PROJECT_REQUIRED
SECRETS_PROVIDER_APIS = _gcp_preflight_plan.SECRETS_PROVIDER_APIS
SERVICE_USAGE_API = _gcp_preflight_plan.SERVICE_USAGE_API
TELEMETRY_EXPORTER_APIS = _gcp_preflight_plan.TELEMETRY_EXPORTER_APIS
TELEMETRY_METRICS_APIS = _gcp_preflight_plan.TELEMETRY_METRICS_APIS
AdcOutcome = _gcp_preflight_plan.AdcOutcome
ApiRequirement = _gcp_preflight_plan.ApiRequirement
CheckResult = _gcp_preflight_plan.CheckResult
CheckStatus = _gcp_preflight_plan.CheckStatus
GoogleAuthBackend = _gcp_preflight_plan.GoogleAuthBackend
api_plan = _gcp_preflight_plan.api_plan
api_requirements = _gcp_preflight_plan.api_requirements
check_gcp_project = _gcp_preflight_plan.check_gcp_project
configuration_problems = _gcp_preflight_plan.configuration_problems
required_apis = _gcp_preflight_plan.required_apis
resolve_ambient_project = _gcp_preflight_plan.resolve_ambient_project
resolve_project = _gcp_preflight_plan.resolve_project

BILLING_ACCOUNT_PATTERN = _gcp_preflight_probes.BILLING_ACCOUNT_PATTERN
GCP_API_NAME_PATTERN = _gcp_preflight_probes.GCP_API_NAME_PATTERN
_open_session = _gcp_preflight_probes.open_session
check_adc_credentials = _gcp_preflight_probes.check_adc_credentials
check_billing = _gcp_preflight_probes.check_billing
check_billing_projects = _gcp_preflight_probes.check_billing_projects
check_required_apis = _gcp_preflight_probes.check_required_apis
check_required_apis_by_project = _gcp_preflight_probes.check_required_apis_by_project
load_google_auth = _gcp_preflight_probes.load_google_auth

_COMPAT_RENAMES: Final[dict[str, object]] = {
    "_ApiProbe": _gcp_preflight_plan.ApiProbe,
    "_accepts_kwarg": _gcp_preflight_plan.accepts_kwarg,
    "_clean": _gcp_preflight_plan.clean_str,
    "_merge_project_results": _gcp_preflight_plan.merge_project_results,
}
_COMPAT_MODULES: Final[tuple[Any, ...]] = (_gcp_preflight_plan, _gcp_preflight_probes)


def __getattr__(name: str) -> Any:
    """Resolve backwards-compatible private facade attributes on demand."""
    if name.startswith("__") and name.endswith("__"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if name in _COMPAT_RENAMES:
        return _COMPAT_RENAMES[name]
    for module in _COMPAT_MODULES:
        namespace = vars(module)
        if name in namespace:
            return namespace[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


logger = logging.getLogger("verify_gcp_prerequisites")

EXIT_OK: Final[int] = 0
EXIT_FAILURE: Final[int] = 1
EXIT_USAGE_ERROR: Final[int] = 2
DEFAULT_REPORT_WIDTH: Final[int] = 78

MIN_TCP_PORT: Final[int] = _gcp_postgres_probe.MIN_TCP_PORT
MAX_TCP_PORT: Final[int] = _gcp_postgres_probe.MAX_TCP_PORT
LIBPQ_DEFAULT_PORT: Final[int] = _gcp_postgres_probe.LIBPQ_DEFAULT_PORT
POSTGRES_SCHEMES = _gcp_postgres_probe.POSTGRES_SCHEMES
URL_AUTHORITY_DELIMITERS = _gcp_postgres_probe.URL_AUTHORITY_DELIMITERS
POSTGRES_QUERY_ENDPOINT_OVERRIDES = _gcp_postgres_probe.POSTGRES_QUERY_ENDPOINT_OVERRIDES
POSTGRES_UNSUPPORTED_HOST_CHARS = _gcp_postgres_probe.POSTGRES_UNSUPPORTED_HOST_CHARS
REDACTED_PORT_PLACEHOLDER = _gcp_postgres_probe.REDACTED_PORT_PLACEHOLDER
PERCENT_ENCODING_HINT = _gcp_postgres_probe.PERCENT_ENCODING_HINT
UNSUPPORTED_ENDPOINT_HINT = _gcp_postgres_probe.UNSUPPORTED_ENDPOINT_HINT

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
    """Run all five pre-flight checks and return their structured outcomes."""
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
    """Format pre-flight check outcomes as a human-readable banner report."""
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
    """Format pre-flight check outcomes as machine-readable JSON."""
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
    """Construct the CLI argument parser with defaults drawn from *settings*."""
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
    """CLI entrypoint for GCP demonstration pre-flight verification."""
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
