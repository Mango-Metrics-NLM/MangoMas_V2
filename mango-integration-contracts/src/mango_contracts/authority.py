"""Keys that must never appear on a cognitive envelope or nested payload.

``extra="forbid"`` rejects unknown *top-level* fields. Nested ``payload`` and
``metadata`` dicts can still smuggle an authority-shaped key; the walker
closes that hole.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import fields, is_dataclass
from typing import Any

from pydantic import BaseModel

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
_CAMEL_LOWER_TO_UPPER = re.compile(r"([a-z0-9])([A-Z])")
_CAMEL_ACRONYM = re.compile(r"([A-Z]+)([A-Z][a-z])")


def normalise_key(key: str) -> str:
    """Unify separators and camelCase so ``allowedTools`` still matches.

    Lowercasing first would erase the camelCase boundary. Split on
    lower→upper and acronym→word, then lowercase, then ``-``/space → ``_``.
    """
    stripped = key.strip()
    split = _CAMEL_LOWER_TO_UPPER.sub(r"\1_\2", stripped)
    split = _CAMEL_ACRONYM.sub(r"\1_\2", split)
    normalised = split.lower().translate(_NORMALISE)
    while "__" in normalised:
        normalised = normalised.replace("__", "_")
    return normalised.strip("_")


def _check_key_and_value(raw_key: Any, child: Any, *, location: str) -> None:
    if not isinstance(raw_key, str):
        raise ValueError(f"{location} keys must be strings")
    normalised = normalise_key(raw_key)
    if normalised in FORBIDDEN_AUTHORITY_KEYS:
        raise ValueError(f"{location} contains forbidden authority key {raw_key!r}")
    if normalised in FORBIDDEN_SECRET_KEYS:
        raise ValueError(f"{location} contains forbidden secret key {raw_key!r}")
    reject_authority_shaped_keys(child, location=f"{location}.{raw_key}")


def _check_validation_alias(alias: Any, *, location: str) -> None:
    if isinstance(alias, str):
        _check_key_and_value(alias, None, location=location)
    elif hasattr(alias, "path"):
        for segment in alias.path:
            if isinstance(segment, str):
                _check_key_and_value(segment, None, location=location)
    else:
        for choice in getattr(alias, "choices", ()):
            _check_validation_alias(choice, location=location)


def _reject_model_keys(value: BaseModel, *, location: str) -> None:
    for field_name, field_info in type(value).model_fields.items():
        _check_key_and_value(field_name, getattr(value, field_name), location=location)
        if field_info.alias is not None:
            _check_key_and_value(field_info.alias, None, location=location)
        if field_info.serialization_alias is not None:
            _check_key_and_value(field_info.serialization_alias, None, location=location)
        if field_info.validation_alias is not None:
            _check_validation_alias(field_info.validation_alias, location=location)
    if value.model_extra:
        for raw_key, child in value.model_extra.items():
            _check_key_and_value(raw_key, child, location=location)


def reject_authority_shaped_keys(value: Any, *, location: str) -> None:
    """Raise ``ValueError`` if *value* nests a forbidden key."""
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            _check_key_and_value(raw_key, child, location=location)
        return
    if isinstance(value, BaseModel):
        _reject_model_keys(value, location=location)
        return
    if is_dataclass(value) and not isinstance(value, type):
        for dc_field in fields(value):
            _check_key_and_value(dc_field.name, getattr(value, dc_field.name), location=location)
        return
    if isinstance(value, AbstractSet | Sequence) and not isinstance(value, str | bytes | bytearray):
        for index, child in enumerate(value):
            reject_authority_shaped_keys(child, location=f"{location}[{index}]")
