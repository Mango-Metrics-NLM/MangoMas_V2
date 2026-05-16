"""Pluggable secret resolution.

The ``SecretsProvider`` protocol decouples ``api_key``-shaped configuration
from how those values are sourced. Today only the environment-variable
backend ships (:class:`EnvSecretsProvider`); cloud-backed providers
(GCP Secret Manager, Vault, etc.) plug in by registering additional
factories on :data:`secrets_registry` without touching agents or adapters.

Resolution happens at orchestrator-build time in
:mod:`mangomas.composition`: when ``LLMSettings.secret_ref`` is set, the
configured ``SecretsSettings.provider`` is looked up in the registry and
its ``get(secret_ref)`` return value replaces the inline ``api_key``.

This module is intentionally minimal — the *seam* is what ships in
this PR, not a full secret-rotation story.
"""

from __future__ import annotations

from mangomas.secrets.env import EnvSecretsProvider
from mangomas.secrets.provider import SecretsProvider
from mangomas.secrets.registry import secrets_registry

__all__ = ["EnvSecretsProvider", "SecretsProvider", "secrets_registry"]
