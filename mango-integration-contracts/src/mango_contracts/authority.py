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
from typing import Any, Literal

from pydantic import BaseModel

_SERIALIZER_DUMP_MODES: tuple[Literal["python", "json"], ...] = ("python", "json")

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


def _check_key_and_value(
    raw_key: Any,
    child: Any,
    *,
    location: str,
    _seen: set[int] | None = None,
) -> None:
    if not isinstance(raw_key, str):
        raise ValueError(f"{location} keys must be strings")
    normalised = normalise_key(raw_key)
    if normalised in FORBIDDEN_AUTHORITY_KEYS:
        raise ValueError(f"{location} contains forbidden authority key {raw_key!r}")
    if normalised in FORBIDDEN_SECRET_KEYS:
        raise ValueError(f"{location} contains forbidden secret key {raw_key!r}")
    _scan_authority_value(child, location=f"{location}.{raw_key}", _seen=_seen)


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


def _reject_model_keys(
    value: BaseModel,
    *,
    location: str,
    _seen: set[int],
) -> None:
    model_cls = type(value)
    for field_name, field_info in model_cls.model_fields.items():
        _check_key_and_value(
            field_name,
            getattr(value, field_name),
            location=location,
            _seen=_seen,
        )
        if field_info.alias is not None:
            _check_key_and_value(field_info.alias, None, location=location, _seen=_seen)
        if field_info.serialization_alias is not None:
            _check_key_and_value(
                field_info.serialization_alias, None, location=location, _seen=_seen
            )
        if field_info.validation_alias is not None:
            _check_validation_alias(field_info.validation_alias, location=location)
    for comp_name, comp_info in model_cls.model_computed_fields.items():
        _check_key_and_value(
            comp_name,
            getattr(value, comp_name),
            location=location,
            _seen=_seen,
        )
        if comp_info.alias is not None:
            _check_key_and_value(comp_info.alias, None, location=location, _seen=_seen)
    if value.model_extra:
        for raw_key, child in value.model_extra.items():
            _check_key_and_value(raw_key, child, location=location, _seen=_seen)
    decorators = model_cls.__pydantic_decorators__
    if decorators.model_serializers or decorators.field_serializers:
        for dump_mode in _SERIALIZER_DUMP_MODES:
            _scan_authority_value(
                value.model_dump(mode=dump_mode),
                location=f"{location}.$serialized",
                _seen=_seen,
            )


def _scan_authority_container(
    value: Any,
    *,
    location: str,
    _seen: set[int],
) -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            _check_key_and_value(raw_key, child, location=location, _seen=_seen)
        return
    if isinstance(value, BaseModel):
        _reject_model_keys(value, location=location, _seen=_seen)
        return
    if is_dataclass(value) and not isinstance(value, type):
        for dc_field in fields(value):
            _check_key_and_value(
                dc_field.name, getattr(value, dc_field.name), location=location, _seen=_seen
            )
        return
    if isinstance(value, AbstractSet | Sequence):
        for index, child in enumerate(value):
            _scan_authority_value(child, location=f"{location}[{index}]", _seen=_seen)


def _scan_authority_value(
    value: Any,
    *,
    location: str,
    _seen: set[int] | None = None,
) -> None:
    if isinstance(value, str | bytes | bytearray):
        return
    seen = set() if _seen is None else _seen
    obj_id = id(value)
    if obj_id in seen:
        return
    seen.add(obj_id)
    try:
        _scan_authority_container(value, location=location, _seen=seen)
    finally:
        seen.discard(obj_id)


def reject_authority_shaped_keys(value: Any, *, location: str) -> None:
    """Raise ``ValueError`` if *value* nests a forbidden key."""
    _scan_authority_value(value, location=location)
