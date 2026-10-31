"""PredicateSpec and the per-kind field rules.

Pure data. Compilation lives in ``_client``; ``json_field`` comparisons live
in ``_json_field``. Neither imports this module's compiler, so the model can
be constructed without pulling the closure machinery in.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_KIND_CONTAINS: Final[str] = "contains"
_KIND_REGEX: Final[str] = "regex"
_KIND_JSON_FIELD: Final[str] = "json_field"
_TEXT_MATCH_KINDS: Final[frozenset[str]] = frozenset({_KIND_CONTAINS, _KIND_REGEX})

KIND_JSON_FIELD: Final[str] = _KIND_JSON_FIELD
TEXT_MATCH_KINDS: Final[frozenset[str]] = _TEXT_MATCH_KINDS
FIELD_PATH_SEPARATOR: Final[str] = "."

_VALUE: Final[str] = "value"
_CASE_SENSITIVE: Final[str] = "case_sensitive"
_FLAGS: Final[str] = "flags"
_FIELD: Final[str] = "field"
_EQUALS: Final[str] = "equals"
_AT_LEAST: Final[str] = "at_least"
_AT_MOST: Final[str] = "at_most"

_COMPARISON_FIELDS: Final[tuple[str, ...]] = (_EQUALS, _AT_LEAST, _AT_MOST)
_NOT_APPLICABLE_TO_TEXT: Final[tuple[str, ...]] = (_FIELD, *_COMPARISON_FIELDS)
_NOT_APPLICABLE_TO_JSON_FIELD: Final[tuple[str, ...]] = (_VALUE, _CASE_SENSITIVE, _FLAGS)
_FIELD_PATH_SEPARATOR: Final[str] = FIELD_PATH_SEPARATOR
_NO_OP_DEFAULT: Final[dict[str, object]] = {_CASE_SENSITIVE: False, _FLAGS: []}


def _carries_instruction(spec: PredicateSpec, name: str) -> bool:
    """True when field *name* says something — not a no-op default.

    ``equals=False`` and ``equals=0`` count as given. ``case_sensitive=False``
    and ``flags=[]`` do not: those are the "say nothing" defaults.
    """
    value = getattr(spec, name)
    return value is not None and value != _NO_OP_DEFAULT.get(name)


class PredicateSpec(BaseModel):
    """Declarative acceptance predicate (``loop.accept`` / ``branch.when``).

    ``contains`` / ``regex`` match response text. ``value`` is required.
    ``case_sensitive`` applies to ``contains``; ``flags`` applies to ``regex``.

    ``json_field`` parses the response as a JSON object and tests one dotted
    path. Exactly one of ``equals`` / ``at_least`` / ``at_most`` selects the
    comparison.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["contains", "regex", "json_field"]
    value: str | None = None
    case_sensitive: bool = False
    flags: list[str] = Field(default_factory=list)
    field: str | None = None
    equals: bool | str | int | float | None = None
    at_least: float | None = None
    at_most: float | None = None

    @model_validator(mode="after")
    def _validate_kind_fields(self) -> PredicateSpec:
        """Enforce the per-kind field rules a single required field cannot.

        Raises ``ValueError``. The workflow loader normalises that to
        ``ConfigError`` at the boundary.
        """
        if self.kind in _TEXT_MATCH_KINDS:
            if not self.value:
                raise ValueError(f"predicate kind {self.kind!r} requires a non-empty {_VALUE!r}")
            self._reject(_NOT_APPLICABLE_TO_TEXT)
            return self

        if not self.field:
            raise ValueError(f"predicate kind {self.kind!r} requires a non-empty {_FIELD!r}")
        self._reject(_NOT_APPLICABLE_TO_JSON_FIELD)
        given = [name for name in _COMPARISON_FIELDS if _carries_instruction(self, name)]
        if not given:
            raise ValueError(
                f"predicate kind {self.kind!r} requires exactly one of "
                f"{list(_COMPARISON_FIELDS)}; none given"
            )
        if len(given) > 1:
            raise ValueError(
                f"predicate kind {self.kind!r} requires exactly one of "
                f"{list(_COMPARISON_FIELDS)}; got {given}"
            )
        return self

    def _reject(self, names: tuple[str, ...]) -> None:
        """Raise when this spec carries a field its kind has no use for."""
        offenders = [name for name in names if _carries_instruction(self, name)]
        if offenders:
            raise ValueError(f"{offenders} not applicable to predicate kind {self.kind!r}")
