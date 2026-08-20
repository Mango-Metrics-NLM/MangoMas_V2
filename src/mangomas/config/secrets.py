"""Secrets-provider settings (`MANGOMAS_SECRETS__*`)."""

from __future__ import annotations

from pydantic import BaseModel

DEFAULT_SECRETS_PROVIDER: str = "env"


# GCP Secret Manager defaults — consumed when MANGOMAS_SECRETS__PROVIDER=gcp.
DEFAULT_GCP_SECRETS_TIMEOUT_SECONDS: float = 5.0


DEFAULT_GCP_SECRET_VERSION: str = "latest"  # noqa: S105 — not a secret value


# Opt-in "fail loud" mode (ADR-0010). Default False preserves ADR-002 (None).
DEFAULT_SECRETS_STRICT: bool = False


class SecretsSettings(BaseModel):
    """Configuration for the SecretsProvider seam."""

    provider: str = DEFAULT_SECRETS_PROVIDER
    # GCP Secret Manager fields (required only when provider="gcp"; validated
    # at factory-build time in composition.py).
    project_id: str | None = None
    timeout_seconds: float = DEFAULT_GCP_SECRETS_TIMEOUT_SECONDS
    default_version: str = DEFAULT_GCP_SECRET_VERSION
    # When True, cloud backends raise SecretsResolutionError on auth/permission/
    # timeout failures instead of returning None (ADR-0010). Default preserves
    # the ADR-002 "collapse to None" local-dev contract.
    strict: bool = DEFAULT_SECRETS_STRICT
