"""SecretsProvider protocol.

Implementations resolve a secret reference (typically an env-var name or a
provider-specific path like ``projects/p/secrets/k/versions/latest``) into a
plaintext value. Returning ``None`` indicates the secret is not configured —
callers should fall back to the inline ``api_key`` setting rather than fail
loudly, so local development continues to work without a vault.

The protocol is intentionally sync-only; cloud backends with async APIs can
be added behind an extension protocol without breaking this surface
(see ADR-001).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SecretsProvider(Protocol):
    """Resolve a secret reference to its plaintext value.

    Parameters
    ----------
    name:
        Backend-specific identifier. For the env-var provider this is the
        environment variable name; for cloud-backed providers it is typically
        a fully-qualified resource path.

    Returns
    -------
    The plaintext secret, or ``None`` when the reference is not configured.
    """

    def get(self, name: str) -> str | None: ...
