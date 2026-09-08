"""Keys that must never appear on a cognitive envelope or nested payload.

``extra="forbid"`` rejects unknown *top-level* fields. Nested ``payload`` and
``metadata`` dicts can still smuggle an authority-shaped key; the walker
closes that hole.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

FORBIDDEN_AUTHORITY_KEYS: frozenset[str] = frozenset(
    {
        "allowed_tools",
        "granted_capabilities",
        "execution_approved",
        "policy_override",
        "release_approved",
        "gate_passed",
        "retry_limit",
        "execution_priority",
        "human_approved",
        "sandbox_bypass",
        "capability_grant",
        # Executable detail belongs on a separately validated ProposedAction,
        # never nested inside cognitive payload/metadata.
        "command",
        "argv",
        "shell",
        "executable",
        "subprocess",
        "fs_handle",
        "file_handle",
    }
)

FORBIDDEN_SECRET_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "bearer_token",
        "password",
        "private_key",
        "authorization",
        "secret",
        "token",
    }
)

_NORMALISE = str.maketrans({"-": "_", " ": "_"})


def normalise_key(key: str) -> str:
    """Lowercase and unify separators so ``Allowed-Tools`` still matches."""
    return key.strip().lower().translate(_NORMALISE)


def reject_authority_shaped_keys(value: Any, *, location: str) -> None:
    """Raise ``ValueError`` if *value* nests a forbidden key."""
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            if not isinstance(raw_key, str):
                raise ValueError(f"{location} keys must be strings")
            normalised = normalise_key(raw_key)
            if normalised in FORBIDDEN_AUTHORITY_KEYS:
                raise ValueError(f"{location} contains forbidden authority key {raw_key!r}")
            if normalised in FORBIDDEN_SECRET_KEYS:
                raise ValueError(f"{location} contains forbidden secret key {raw_key!r}")
            reject_authority_shaped_keys(child, location=f"{location}.{raw_key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            reject_authority_shaped_keys(child, location=f"{location}[{index}]")
