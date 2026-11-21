"""Static validation, project resolution, and API planning for GCP pre-flight."""

from __future__ import annotations

import enum
import inspect
import logging
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from mangomas.config import Settings

logger = logging.getLogger("verify_gcp_prerequisites")

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
    """The slice of ``google-auth`` pre-flight uses; injectable for tests."""

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
class ApiProbe:
    """Service Usage outcome for one project's APIs."""

    disabled: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)


_STATUS_SEVERITY: Final[dict[CheckStatus, int]] = {
    CheckStatus.PASS: 0,
    CheckStatus.SKIP: 1,
    CheckStatus.FAIL: 2,
}


def clean_str(value: str | None) -> str | None:
    """Strip whitespace; treat empty as unset."""
    cleaned = value.strip() if value else ""
    return cleaned or None


def accepts_kwarg(fn: Callable[..., Any], kwarg: str) -> bool:
    """True when ``fn`` accepts ``kwarg`` by name or ``**kwargs``."""
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        param.kind is inspect.Parameter.VAR_KEYWORD or param.name == kwarg for param in params
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
    return next((p for p in map(clean_str, candidates) if p), None)


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
    return next((p for p in map(clean_str, candidates) if p), None)


def _extract_resource_project(raw_ref: str | None) -> str | None:
    """Extract valid ``<project>`` from ``projects/<project>/secrets/<id>[/versions/<v>]``."""
    cleaned = clean_str(raw_ref)
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
        if not clean_str(settings.secrets.project_id):
            problems.append(SECRETS_PROJECT_REQUIRED)
        for raw_ref in _configured_resource_refs(settings):
            cleaned = clean_str(raw_ref)
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
    projects = list(dict.fromkeys([project, *(p for p in map(clean_str, extra_projects) if p)]))
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
            project = clean_str(explicit_project) or ambient_project
            requirements.append(ApiRequirement(api=api, project=project, source=source))

    llm_project = clean_str(settings.llm.project_id)
    add(LLM_PROVIDER_APIS.get(settings.llm.provider), llm_project, "llm")
    for profile_name, profile in sorted(settings.llm_profiles.items()):
        add(
            LLM_PROVIDER_APIS.get(profile.provider),
            clean_str(profile.project_id) or llm_project,
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
    for api in map(clean_str, extra):
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


def merge_project_results(name: str, per_project: Mapping[str, CheckResult]) -> CheckResult:
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
