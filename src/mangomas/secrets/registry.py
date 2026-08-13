"""Module-level :class:`Registry` of :class:`SecretsProvider` implementations.

Seeded with the env-var backend at import time. The GCP Secret Manager backend
(:mod:`mangomas.secrets.gcp`) *does* ship: it is registered lazily by
:func:`mangomas.composition.ensure_secrets_provider` when
``MANGOMAS_SECRETS__PROVIDER=gcp``, keeping its SDK import behind the ``gcp``
extra. Unlike the provider registries in ``composition``, this one stores
provider **instances**, not factories.
"""

from __future__ import annotations

from mangomas.registry import Registry
from mangomas.secrets.env import EnvSecretsProvider
from mangomas.secrets.provider import SecretsProvider

secrets_registry: Registry[SecretsProvider] = Registry("secrets")
secrets_registry.register("env", EnvSecretsProvider())
