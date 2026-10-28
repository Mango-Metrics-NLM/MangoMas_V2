"""Deploy-domain test constants (ADR-0036 environment registry).

The env-var names are *derived* from ``Settings``' own prefix and nested
delimiter rather than restated, so renaming either breaks these at import time
instead of leaving the deploy suites asserting against a stale spelling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from mangomas.config import Settings

_PREFIX: Final[str] = str(Settings.model_config.get("env_prefix", ""))
_DELIMITER: Final[str] = str(Settings.model_config.get("env_nested_delimiter", ""))

# The top-level `env` field's variable: the per-environment label.
ENV_LABEL_VAR: Final[str] = f"{_PREFIX}ENV"
# The base manifest's one secretKeyRef-backed variable.
LLM_API_KEY_VAR: Final[str] = f"{_PREFIX}LLM{_DELIMITER}API_KEY"

# Placeholder values the render step substitutes (deploy/environments.yaml uses
# ${PROJECT_ID}); a recognisably fake project keeps rendered output obviously
# test-only.
PROJECT_ID_PLACEHOLDER_VAR: Final[str] = "PROJECT_ID"
TEST_PROJECT_ID: Final[str] = "test-project-0000"
# The Vertex model id placeholder (deploy.yml fills it from `vars.VERTEX_MODEL`).
LLM_MODEL_PLACEHOLDER_VAR: Final[str] = "LLM_MODEL"
TEST_LLM_MODEL: Final[str] = "test-vertex-model"
# Every render placeholder with a test value — the Makefile's
# DEPLOY_RENDER_ENV_VARS must name exactly these (a deploy test checks it), so
# a render test that passes them all can never fail on an unfilled `${...}`.
RENDER_PLACEHOLDER_VALUES: Final[dict[str, str]] = {
    PROJECT_ID_PLACEHOLDER_VAR: TEST_PROJECT_ID,
    LLM_MODEL_PLACEHOLDER_VAR: TEST_LLM_MODEL,
}
TEST_IMAGE_REF: Final[str] = (
    "test-region-docker.pkg.dev/test-project-0000/test-repo/test-image@sha256:" + "0" * 64
)

# The build arg selecting the extras the production image installs, and the
# extra the reference manifest's providers need (pyproject's `cloud` meta-extra
# = vertex + postgres + gcp). The Makefile's LOCK_EXTRAS and the Dockerfile's
# ARG default are both held to this name by tests/deploy/.
IMAGE_EXTRAS_BUILD_ARG: Final[str] = "MANGOMAS_EXTRAS"
CLOUD_EXTRA: Final[str] = "cloud"

# A pin that exists only when the lock was compiled on the image's platform:
# uvicorn[standard] pulls uvloop under `sys_platform != "win32"`, and
# pip-compile evaluates that marker on the compiling host. Its absence is the
# fingerprint of a Windows-compiled lock, which the Linux image would then
# resolve unconstrained.
LINUX_ONLY_LOCK_CANARY: Final[str] = "uvloop"


def _var(group: str, field: str) -> str:
    return f"{_PREFIX}{group}{_DELIMITER}{field}"


# Settings the reference manifest must carry for its safe defaults (spec-0038).
AUTH_ENABLED_VAR: Final[str] = _var("AUTH", "ENABLED")
AUTH_SECRET_REF_VAR: Final[str] = _var("AUTH", "SECRET_REF")
API_MAX_BODY_BYTES_VAR: Final[str] = _var("API", "MAX_BODY_BYTES")
API_MAX_CONCURRENT_REQUESTS_VAR: Final[str] = _var("API", "MAX_CONCURRENT_REQUESTS")
DB_URL_VAR: Final[str] = _var("DB", "URL")


@dataclass(frozen=True)
class ProviderRequirement:
    """What selecting one provider in the manifest commits the deploy to.

    ``selector`` is the ``(env var, value)`` pair that selects the provider;
    ``extra`` the pyproject extra the image must install for it; ``required``
    the settings the provider cannot run without (composition raises or the
    adapter dials a default endpoint otherwise); ``smoke_module`` the SDK
    module whose lazy import fails in an image without the extra, which
    `make docker-smoke` imports (IMAGE_SMOKE_IMPORTS).
    """

    name: str
    selector: tuple[str, str]
    extra: str
    required: tuple[str, ...]
    smoke_module: str


# One row per lazily-imported cloud SDK. Rows are consulted, never restated:
# tests/deploy/test_manifest_provider_contract.py selects the rows whose
# selector the manifest sets and checks the rest of the row against the image
# (Dockerfile extras), the manifest (required settings) and the Makefile
# (IMAGE_SMOKE_IMPORTS).
PROVIDER_REQUIREMENTS: Final[tuple[ProviderRequirement, ...]] = (
    ProviderRequirement(
        name="vertex",
        selector=(_var("LLM", "PROVIDER"), "vertex"),
        extra="vertex",
        # `project_id` is forwarded to vertexai.init; `model` must not be the
        # LM Studio default `local-model`.
        required=(_var("LLM", "PROJECT_ID"), _var("LLM", "MODEL")),
        smoke_module="vertexai",
    ),
    ProviderRequirement(
        name="postgres",
        selector=(_var("DB", "PROVIDER"), "postgres"),
        extra="postgres",
        # Without it DBSettings.url keeps the SQLite default.
        required=(DB_URL_VAR,),
        smoke_module="asyncpg",
    ),
    ProviderRequirement(
        name="gcp-secrets",
        selector=(_var("SECRETS", "PROVIDER"), "gcp"),
        extra="gcp",
        # composition/secrets.py raises ConfigError without it.
        required=(_var("SECRETS", "PROJECT_ID"),),
        smoke_module="google.cloud.secretmanager",
    ),
    ProviderRequirement(
        name="gcp-trace",
        selector=(_var("TELEMETRY", "EXPORTER"), "gcp"),
        extra="gcp",
        # The exporter resolves the project from ADC; nothing else required.
        required=(),
        smoke_module="opentelemetry.exporter.cloud_trace",
    ),
    ProviderRequirement(
        name="gcp-monitoring",
        # Conservative: METRICS_ENABLED=true needs the exporter only together
        # with EXPORTER=gcp, but a row selected by one pair is simpler to
        # reason about, and the manifest leaves metrics off (alpha-only SDK).
        selector=(_var("TELEMETRY", "METRICS_ENABLED"), "true"),
        extra="gcp-monitoring",
        required=(),
        smoke_module="opentelemetry.exporter.cloud_monitoring",
    ),
)
