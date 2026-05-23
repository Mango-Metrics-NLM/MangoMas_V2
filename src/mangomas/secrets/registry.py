"""Module-level :class:`Registry` of :class:`SecretsProvider` implementations.

Seeded with the env-var backend at import time. Cloud backends (GCP Secret
Manager, Vault, etc.) register themselves here in their own composition
modules — none ship in this release.
"""

from __future__ import annotations

from mangomas.registry import Registry
from mangomas.secrets.env import EnvSecretsProvider
from mangomas.secrets.provider import SecretsProvider

secrets_registry: Registry[SecretsProvider] = Registry("secrets")
secrets_registry.register("env", EnvSecretsProvider())
