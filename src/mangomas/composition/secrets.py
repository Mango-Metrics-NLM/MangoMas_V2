"""Secrets provider factories and resolution helpers.

This module handles secrets provider bootstrap and LLM-specific secret resolution.
Lazy-imports cloud backends (GCP Secret Manager) so the optional extras stay
optional — the import only fires when the corresponding provider is selected
via Settings.
"""

from __future__ import annotations

import logging
from typing import Any

from mangomas.config import LLMSettings, SecretsSettings
from mangomas.errors import ConfigError

logger = logging.getLogger(__name__)


def _resolve_llm_secrets(llm_cfg: LLMSettings, secrets_provider_name: str) -> LLMSettings:
    """Return *llm_cfg* with ``api_key`` resolved via the SecretsProvider seam.

    When ``llm_cfg.secret_ref`` is unset, *llm_cfg* is returned unchanged. When
    set, the named secrets provider is looked up and its ``get(secret_ref)``
    return value replaces ``api_key`` on a fresh copy of the settings model.
    If the provider returns ``None`` the inline ``api_key`` is kept — local
    development with no env var configured continues to work without a vault.
    """
    if not llm_cfg.secret_ref:
        return llm_cfg
    import mangomas.composition as composition_module  # noqa: PLC0415

    provider = composition_module.secrets_registry.get(secrets_provider_name)
    resolved = provider.get(llm_cfg.secret_ref)
    if resolved is None:
        logger.debug(
            "secret_ref %r not resolved by provider %r; keeping inline api_key",
            llm_cfg.secret_ref,
            secrets_provider_name,
        )
        return llm_cfg
    return llm_cfg.model_copy(update={"api_key": resolved})


def _build_gcp_secrets_provider(cfg: SecretsSettings) -> Any:
    """Build a GCPSecretManagerProvider from SecretsSettings; requires ``project_id``."""
    if not cfg.project_id:
        raise ConfigError(
            "MANGOMAS_SECRETS__PROJECT_ID is required when MANGOMAS_SECRETS__PROVIDER='gcp'."
        )
    from mangomas.secrets.gcp import GCPSecretManagerProvider  # noqa: PLC0415

    return GCPSecretManagerProvider(
        project_id=cfg.project_id,
        timeout_seconds=cfg.timeout_seconds,
        default_version=cfg.default_version,
        strict=cfg.strict,
    )


def ensure_secrets_provider(cfg: SecretsSettings) -> None:
    """Register the configured secrets backend if it is not already present.

    Idempotent, and public because **two** entry points need it, not one.
    ``build_orchestrator`` runs inside the FastAPI lifespan, but
    ``create_app`` resolves the expected API token during app *construction* —
    strictly earlier. Registering only in ``build_orchestrator`` therefore left
    ``resolve_auth_state`` looking up an unregistered ``gcp`` provider, which
    fails closed to ``expected_token=None`` and 401s every request on a
    correctly configured GCP + auth deployment.

    Only cloud backends need this: ``env`` is seeded at import time by
    :mod:`mangomas.secrets.registry`. Keeps the registry's "stored as instance,
    not factory" contract.
    """
    import mangomas.composition as composition_module  # noqa: PLC0415

    if cfg.provider == "gcp" and "gcp" not in composition_module.secrets_registry.available():
        logger.info("Registering GCP secrets provider", extra={"project_id": cfg.project_id})
        composition_module.secrets_registry.register(
            "gcp",
            composition_module._build_gcp_secrets_provider(cfg),  # noqa: SLF001
        )


__all__ = [
    "_build_gcp_secrets_provider",
    "_resolve_llm_secrets",
    "ensure_secrets_provider",
]
