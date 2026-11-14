"""Regression + AQA guards for ``mango_contracts.authority`` (spec-0050 D-7)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import (
    AliasChoices,
    AliasPath,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_serializer,
    model_serializer,
)

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


class _ComputedFieldDirectName(BaseModel):
    ok: str = "x"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def allowed_tools(self) -> list[str]:
        return ["bash"]


class _ComputedFieldAlias(BaseModel):
    ok: str = "x"

    @computed_field(alias="capability_grant")  # type: ignore[prop-decorator]
    @property
    def computed_label(self) -> str:
        return "yes"


class _NestedSerializationAlias(BaseModel):
    internal: str = Field(default="yes", serialization_alias="execution_approved")


class _ComputedFieldNestedModel(BaseModel):
    ok: str = "x"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def computed_meta(self) -> _NestedSerializationAlias:
        return _NestedSerializationAlias()


class _ComputedFieldNestedPayload(BaseModel):
    ok: str = "x"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def summary(self) -> dict[str, str]:
        return {"api_key": "leaked"}


class _CustomSerializerModel(BaseModel):
    safe: str = "ok"

    @model_serializer
    def _dump(self) -> dict[str, object]:
        return {"safe": self.safe, "granted_capabilities": ["shell"]}


class _JsonWhenUsedModelSerializer(BaseModel):
    safe: str = "ok"

    @model_serializer(when_used="json")
    def _dump_json(self) -> dict[str, object]:
        return {"safe": self.safe, "sandbox_bypass": True}


class _FieldSerializerPayload(BaseModel):
    payload: str = "safe"

    @field_serializer("payload")
    def _serialize_payload(self, value: str) -> dict[str, str]:
        return {"note": value, "execution_approved": "true"}


class _SafeFieldSerializerPayload(BaseModel):
    payload: str = "safe"

    @field_serializer("payload")
    def _serialize_payload(self, value: str) -> dict[str, str]:
        return {"note": value}


@dataclass
class _CyclicDataclass:
    label: str = "ok"
    child: Any = None


class _SafeComputedAndSerializedModel(BaseModel):
    safe: str = "ok"

    @computed_field(alias="public_label")  # type: ignore[prop-decorator]
    @property
    def internal_label(self) -> dict[str, str]:
        return {"note": self.safe}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def plain_note(self) -> str:
        return self.safe


class _CyclicSafeModel(BaseModel):
    label: str = "ok"
    child: _CyclicSafeModel | None = None


def test_d03_authority_key_guard_inspects_computed_fields_and_model_serializer() -> None:
    """Computed fields, Python/JSON serializers, and cyclic containers are safely scanned."""
    for model in (
        _ComputedFieldDirectName(),
        _ComputedFieldAlias(),
        _ComputedFieldNestedModel(),
        _ComputedFieldNestedPayload(),
        _CustomSerializerModel(),
        _JsonWhenUsedModelSerializer(),
        _FieldSerializerPayload(),
    ):
        with pytest.raises(ValueError, match="forbidden"):
            reject_authority_shaped_keys(model, location="envelope")
    reject_authority_shaped_keys(
        _SafeComputedAndSerializedModel(),
        location="envelope",
    )
    reject_authority_shaped_keys(
        _SafeFieldSerializerPayload(),
        location="envelope",
    )
    cyclic = _CyclicSafeModel()
    cyclic.child = cyclic
    reject_authority_shaped_keys(cyclic, location="envelope")

    cyclic_dict: dict[str, Any] = {"ok": "val"}
    cyclic_dict["self"] = cyclic_dict
    reject_authority_shaped_keys(cyclic_dict, location="envelope")

    cyclic_list: list[Any] = ["ok"]
    cyclic_list.append(cyclic_list)
    reject_authority_shaped_keys(cyclic_list, location="envelope")

    cyclic_dc = _CyclicDataclass()
    cyclic_dc.child = cyclic_dc
    reject_authority_shaped_keys(cyclic_dc, location="envelope")
