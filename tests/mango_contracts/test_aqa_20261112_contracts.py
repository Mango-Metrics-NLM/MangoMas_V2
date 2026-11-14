"""Regression + AQA guards for ``mango_contracts.authority`` (spec-0050 D-7)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import AliasChoices, AliasPath, BaseModel, ConfigDict, Field

from mango_contracts.authority import reject_authority_shaped_keys


@dataclass(frozen=True)
class _UnsafeDataclass:
    allowed_tools: tuple[str, ...] = ("bash",)


@dataclass(frozen=True)
class _SafeDataclass:
    label: str
    nested: object = None


class _ModelWithSecretAttr(BaseModel):
    api_key: str


class _ModelWithExtras(BaseModel):
    model_config = ConfigDict(extra="allow")
    summary: str


class _SafeModel(BaseModel):
    summary: str


def test_d7_rejects_authority_or_secret_keys_inside_dataclass() -> None:
    """Nested ``@dataclass`` fields carrying authority/secret names must be rejected."""
    with pytest.raises(ValueError, match="allowed_tools"):
        reject_authority_shaped_keys(_UnsafeDataclass(), location="payload")
    with pytest.raises(ValueError, match="allowed_tools"):
        reject_authority_shaped_keys(
            _SafeDataclass(label="outer", nested={"child": _UnsafeDataclass()}),
            location="payload",
        )


def test_d7_rejects_authority_or_secret_keys_inside_pydantic_model_and_extras() -> None:
    """Nested ``BaseModel`` fields and ``model_extra`` keys must be inspected."""
    with pytest.raises(ValueError, match="api_key"):
        reject_authority_shaped_keys(_ModelWithSecretAttr(api_key="x"), location="payload")
    with pytest.raises(ValueError, match="sandbox_bypass"):
        reject_authority_shaped_keys(
            _ModelWithExtras.model_validate({"summary": "ok", "sandbox_bypass": True}),
            location="payload",
        )


def test_d7_rejects_nested_elements_inside_sets() -> None:
    """``set``/``frozenset`` elements must be walked recursively."""
    with pytest.raises(ValueError, match="allowed_tools"):
        reject_authority_shaped_keys(
            frozenset({_UnsafeDataclass()}),
            location="payload",
        )


def test_d7_accepts_safe_dataclass_model_and_set() -> None:
    """Positive boundary: benign dataclass, BaseModel (+ extras), and frozenset pass."""
    reject_authority_shaped_keys(
        {
            "dc": _SafeDataclass(label="clean", nested=_SafeModel(summary="ok")),
            "extras": _ModelWithExtras.model_validate({"summary": "ok", "note": "safe"}),
            "items": frozenset({"a", "b"}),
        },
        location="payload",
    )


class _ModelWithForbiddenAlias(BaseModel):
    safe_name: str = Field(alias="allowed_tools")


class _ModelWithForbiddenSerAlias(BaseModel):
    safe_name: str = Field(serialization_alias="api_key")


class _ModelWithForbiddenValAliasStr(BaseModel):
    safe_name: str = Field(validation_alias="policy_override")


class _ModelWithForbiddenValAliasPath(BaseModel):
    safe_name: str = Field(validation_alias=AliasPath("outer", 0, "sandbox_bypass"))


class _ModelWithForbiddenValAliasChoices(BaseModel):
    safe_name: str = Field(
        validation_alias=AliasChoices("ok_label", AliasPath("outer", 0, "bearer_token"))
    )


class _ModelWithSafeAliases(BaseModel):
    safe_name: str = Field(
        alias="display_name",
        serialization_alias="summary_label",
        validation_alias=AliasChoices("short_name", AliasPath("meta", 0, "tag")),
    )


def test_d7_critic_rejects_authority_keys_in_pydantic_aliases() -> None:
    """Finding #6: alias/serialization_alias/validation_alias must be checked."""
    for model in (
        _ModelWithForbiddenAlias(allowed_tools="x"),
        _ModelWithForbiddenSerAlias(safe_name="x"),
        _ModelWithForbiddenValAliasStr.model_validate({"policy_override": "x"}),
        _ModelWithForbiddenValAliasPath.model_validate({"outer": [{"sandbox_bypass": "x"}]}),
        _ModelWithForbiddenValAliasChoices.model_validate({"ok_label": "x"}),
    ):
        with pytest.raises(ValueError, match="forbidden"):
            reject_authority_shaped_keys(model, location="envelope")
    reject_authority_shaped_keys(
        _ModelWithSafeAliases.model_validate({"short_name": "ok"}),
        location="envelope",
    )
