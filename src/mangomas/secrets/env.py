"""Environment-variable :class:`SecretsProvider` implementation."""

from __future__ import annotations

import os


class EnvSecretsProvider:
    """Reads secrets from ``os.environ``.

    The argument to :meth:`get` is interpreted as an environment variable
    name. This keeps local development frictionless: developers set the
    variable in their shell or ``.env`` and reference it from settings
    via ``LLMSettings.secret_ref="MY_API_KEY"``.
    """

    def get(self, name: str) -> str | None:
        """Return ``os.environ.get(name)``."""
        return os.environ.get(name)
