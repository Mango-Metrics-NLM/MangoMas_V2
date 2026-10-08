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
