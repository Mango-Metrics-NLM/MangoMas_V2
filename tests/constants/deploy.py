"""Deploy-domain test constants (ADR-0036 environment registry).

The env-var names are *derived* from ``Settings``' own prefix and nested
delimiter rather than restated, so renaming either breaks these at import time
instead of leaving the deploy suites asserting against a stale spelling.
"""

from __future__ import annotations

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
