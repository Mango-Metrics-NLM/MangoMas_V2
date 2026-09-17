"""Declarative acceptance predicates compiled to a synchronous ``AcceptanceFn``.

A :class:`PredicateSpec` is pure data (JSON-friendly) describing how to decide
whether an agent response "accepts" — used by the ``loop`` workflow node to build
the ``acceptance_fn`` that :meth:`mangomas.core.Orchestrator.dispatch` consumes,
and by the ``branch`` node to route. The predicate is compiled once into a plain
**synchronous** closure; it must stay sync (no I/O, no ``await``) because
``AcceptanceFn`` is a ``Callable[[AgentResponse], bool]`` the dispatch loop calls
inline.

Three kinds. ``contains`` and ``regex`` match the response **text**;
``json_field`` (spec 0032) parses the response as a JSON **object** and tests one
addressed field. The third kind exists because the structured agents do not emit
text: ``ReviewerAgent`` emits a ``ReviewResult`` and ``PlannerAgent`` an
``ExecutionPlan``, and *no* spelling of a substring match over their serialised
output is correct. A quoted needle (``'"passed": true'``) is a false **negative**
— ``model_dump_json()`` emits no space after the colon, and pretty-printing moves
it again — while the quoteless needle that survives formatting drift is a false
**positive**, matching prose inside ``feedback`` / ``suggestions`` and accepting a
*rejecting* review. Binding acceptance to a parsed field fails in neither
direction.

The regex-flag map mirrors :mod:`mangomas.eval.scorers.regex_match` but is copied
here deliberately: the ``workflow`` package is a pure-domain sibling of ``eval``
and must not import ``eval`` internals. ``mangomas.core`` is a *lower* layer, so
reusing :func:`~mangomas.core.structured.parse_llm_json_object` is an ordinary
import, not a layering violation.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mangomas.core.structured import parse_llm_json_object
from mangomas.errors import ConfigError, LLMBadResponse

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import AgentResponse
    from mangomas.core.loop import AcceptanceFn

logger = logging.getLogger(__name__)

# Copied (not imported) from eval/scorers/regex_match.py so ``workflow`` carries
# no dependency on the ``eval`` package.
_FLAG_BY_NAME: dict[str, re.RegexFlag] = {
    "ignorecase": re.IGNORECASE,
    "multiline": re.MULTILINE,
    "dotall": re.DOTALL,
}

# ── Predicate kinds ───────────────────────────────────────────────────────────

_KIND_CONTAINS: Final[str] = "contains"
_KIND_REGEX: Final[str] = "regex"
_KIND_JSON_FIELD: Final[str] = "json_field"
#: The kinds that match response *text* — everything else addresses parsed JSON.
_TEXT_MATCH_KINDS: Final[frozenset[str]] = frozenset({_KIND_CONTAINS, _KIND_REGEX})

# ── Model field names (R8) ────────────────────────────────────────────────────
# Named once so the per-kind applicability sets and every error message derive
# from the same strings, following the ``_FLAG_BY_NAME`` precedent above.

_VALUE: Final[str] = "value"
_CASE_SENSITIVE: Final[str] = "case_sensitive"
_FLAGS: Final[str] = "flags"
_FIELD: Final[str] = "field"
_EQUALS: Final[str] = "equals"
_AT_LEAST: Final[str] = "at_least"
_AT_MOST: Final[str] = "at_most"

#: Exactly one of these selects a ``json_field`` comparison.
_COMPARISON_FIELDS: Final[tuple[str, ...]] = (_EQUALS, _AT_LEAST, _AT_MOST)
#: Fields a ``contains`` / ``regex`` spec must not carry.
_NOT_APPLICABLE_TO_TEXT: Final[tuple[str, ...]] = (_FIELD, *_COMPARISON_FIELDS)
#: Fields a ``json_field`` spec must not carry.
_NOT_APPLICABLE_TO_JSON_FIELD: Final[tuple[str, ...]] = (_VALUE, _CASE_SENSITIVE, _FLAGS)

#: Separator for the dotted ``field`` path: ``"review.passed"`` → two segments,
#: ``"passed"`` → the one-segment case.
_FIELD_PATH_SEPARATOR: Final[str] = "."

#: Defaults that instruct nothing. ``case_sensitive`` / ``flags`` default to
#: ``False`` / ``[]`` rather than ``None``, so "was it given?" cannot be a bare
#: ``is not None`` test for them. Compared with ``!=`` and never truthiness:
#: ``equals`` is legitimately ``False``, ``0`` or ``""``. Value-based rather
#: than ``model_fields_set``-based on purpose — a field re-stated at its own
#: default is a no-op, and this is what keeps ``model_dump()`` output
#: re-validatable.
_NO_OP_DEFAULT: Final[dict[str, object]] = {_CASE_SENSITIVE: False, _FLAGS: []}

# ── Structured-log events ─────────────────────────────────────────────────────

_COMPILE_EVENT: Final[str] = "workflow_predicate_compiled"
_EVALUATE_EVENT: Final[str] = "workflow_predicate_evaluated"


def _carries_instruction(spec: PredicateSpec, name: str) -> bool:
    """True when field *name* on *spec* says something — i.e. is not a no-op.

    For every ``None``-defaulted field this is exactly ``is not None``, which is
    why ``equals=False`` and ``equals=0`` count as given.
    """
    value = getattr(spec, name)
    return value is not None and value != _NO_OP_DEFAULT.get(name)


class PredicateSpec(BaseModel):
    """Declarative acceptance predicate (``loop.accept`` / ``branch.when``).

    The applicable fields differ per kind:

    ``contains`` / ``regex``
        Match the response text. ``value`` is required and non-empty;
        ``case_sensitive`` applies to ``contains`` only (the substring match is
        case-insensitive unless set ``True``) and ``flags`` to ``regex`` only
        (any of ``ignorecase`` / ``multiline`` / ``dotall``, OR-combined).

    ``json_field``
        Parse the response as a JSON object and test one addressed field.
        ``field`` is a dotted path over mappings and is required; exactly one of
        ``equals`` / ``at_least`` / ``at_most`` selects the comparison.

    ``value`` is optional at the schema level only because ``json_field`` has no
    use for it. The model validator below restores the "required and non-empty"
    guard for the two text kinds, so no spelling rejected before spec-0032
    validates now — relaxing the field without it would silently widen the
    schema, which is this change's one real regression risk.
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

        Raises ``ValueError``, which pydantic reports as a ``ValidationError``.
        A graph reaches this through :func:`~mangomas.workflow.loader.load_workflow`,
        which normalises that to :class:`~mangomas.errors.ConfigError` (400) at
        the loader boundary — so an operator still sees one error type.
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


# ── json_field comparisons ────────────────────────────────────────────────────

#: A compiled comparison over the value found at the addressed path.
_Comparison = Callable[[Any], bool]


def _as_number(found: Any) -> float | None:
    """Return *found* as a float, or ``None`` when it is not a number.

    ``bool`` is rejected first and deliberately (R4): ``True >= 0.5`` is
    otherwise silently true in Python, which would let a boolean field satisfy a
    score threshold.
    """
    if isinstance(found, bool):
        return None
    if isinstance(found, int | float):
        return float(found)
    return None


def _build_equals(expected: Any) -> _Comparison:
    def _equals(found: Any) -> bool:
        # ``True == 1`` and ``False == 0`` in Python, so bool-ness is compared
        # before equality, both ways: ``equals: true`` must not be satisfied by
        # the integer ``1``, and ``equals: 1`` must not be satisfied by ``True``.
        if (type(found) is bool) is not (type(expected) is bool):
            return False
        return bool(found == expected)

    return _equals


def _build_at_least(threshold: Any) -> _Comparison:
    def _at_least(found: Any) -> bool:
        number = _as_number(found)
        return number is not None and number >= threshold

    return _at_least


def _build_at_most(threshold: Any) -> _Comparison:
    def _at_most(found: Any) -> bool:
        number = _as_number(found)
        return number is not None and number <= threshold

    return _at_most


#: Keyed by comparison field name, so the dispatch and the validator's
#: exactly-one rule read from the same vocabulary (pinned equal by a test).
_COMPARISON_BUILDERS: dict[str, Callable[[Any], _Comparison]] = {
    _EQUALS: _build_equals,
    _AT_LEAST: _build_at_least,
    _AT_MOST: _build_at_most,
}


def _build_comparison(spec: PredicateSpec) -> _Comparison:
    """Select the single comparison *spec* names, or raise ``ConfigError``.

    The model validator normally makes this unreachable; it stays as the
    compile-time guard (R3) for a spec that bypassed validation, so no broken
    spec can ever yield a closure.
    """
    given = [name for name in _COMPARISON_FIELDS if getattr(spec, name) is not None]
    if len(given) != 1:
        raise ConfigError(
            f"predicate kind {_KIND_JSON_FIELD!r} requires exactly one of "
            f"{list(_COMPARISON_FIELDS)}; got {given}"
        )
    name = given[0]
    return _COMPARISON_BUILDERS[name](getattr(spec, name))


def _parse_object(content: str) -> dict[str, Any] | None:
    """Strict, whole-text JSON-object parse; ``None`` when *content* is not one.

    Strict (:func:`~mangomas.core.structured.parse_llm_json_object`) rather than
    ``parse_or_recover`` by decision (R5): ``StructuredOutputAgent.parse`` — the
    implementation behind ``MANGOMAS_AGENTS__<NAME>__VALIDATE_OUTPUT`` — is
    strict, and a recovering predicate would accept what that guard rejects.
    """
    try:
        return parse_llm_json_object(content)
    except LLMBadResponse:
        return None


def _resolve(data: Mapping[str, Any], segments: tuple[str, ...]) -> tuple[bool, Any]:
    """Walk the dotted path over mappings; ``(False, None)`` when it runs off.

    A missing key, a non-mapping intermediate, or a path longer than the
    document is "not resolved" — never an exception (R2).
    """
    current: Any = data
    for segment in segments:
        if not isinstance(current, Mapping) or segment not in current:
            return False, None
        current = current[segment]
    return True, current


# ── Compilation ───────────────────────────────────────────────────────────────


def _resolve_flags(names: list[str]) -> int:
    flags = 0
    for name in names:
        flag = _FLAG_BY_NAME.get(name.lower())
        if flag is None:
            raise ConfigError(f"unknown regex flag {name!r}; valid: {sorted(_FLAG_BY_NAME)}")
        flags |= flag
    return flags


def _required_value(spec: PredicateSpec) -> str:
    """Return the text needle, or raise ``ConfigError``.

    The compile-time half of the guard the now-optional ``value`` field used to
    give for free: a spec that bypassed validation must not compile to a closure
    matching the empty string, which would accept everything.
    """
    if not spec.value:
        raise ConfigError(f"predicate kind {spec.kind!r} requires a non-empty {_VALUE!r}")
    return spec.value


def _compile_contains(spec: PredicateSpec) -> AcceptanceFn:
    case_sensitive = spec.case_sensitive
    raw = _required_value(spec)
    needle = raw if case_sensitive else raw.lower()

    def _contains(response: AgentResponse) -> bool:
        haystack = response.content if case_sensitive else response.content.lower()
        return needle in haystack

    return _contains


def _compile_regex(spec: PredicateSpec) -> AcceptanceFn:
    flags = _resolve_flags(spec.flags)
    raw = _required_value(spec)
    try:
        pattern = re.compile(raw, flags)
    except re.error as exc:
        raise ConfigError(f"invalid regex pattern {raw!r}: {exc}") from exc

    def _regex(response: AgentResponse) -> bool:
        return pattern.search(response.content) is not None

    return _regex


def _compile_json_field(spec: PredicateSpec) -> AcceptanceFn:
    path = spec.field
    if not path:
        raise ConfigError(f"predicate kind {_KIND_JSON_FIELD!r} requires a non-empty {_FIELD!r}")
    compare = _build_comparison(spec)
    segments = tuple(path.split(_FIELD_PATH_SEPARATOR))

    def _json_field(response: AgentResponse) -> bool:
        data = _parse_object(response.content)
        resolved, found = (False, None) if data is None else _resolve(data, segments)
        verdict = resolved and compare(found)
        # DEBUG only — this runs once per acceptance-loop step. Never carries
        # response content: the addressed path, what happened to it, and the
        # verdict are what a failing loop needs.
        logger.debug(
            "workflow json_field predicate evaluated",
            extra={
                "event": _EVALUATE_EVENT,
                "predicate_kind": _KIND_JSON_FIELD,
                "field_path": path,
                "parsed": data is not None,
                "resolved": resolved,
                "verdict": verdict,
            },
        )
        return verdict

    return _json_field


def compile_predicate(spec: PredicateSpec) -> AcceptanceFn:
    """Compile *spec* into a pure, synchronous ``AcceptanceFn``.

    Raises :class:`~mangomas.errors.ConfigError` on an invalid regex flag, an
    uncompilable pattern, or a ``json_field`` spec whose ``field`` / comparison
    is not exactly right — at compile time, not per call. The per-kind rules
    normally fail earlier still, as a ``ValidationError`` at construction; the
    checks here are what a spec built with validation bypassed hits instead.

    The returned closure is **total**: no response content makes it raise.
    Unparseable content, a non-object JSON document, or a path that does not
    resolve is simply "not accepted", so non-convergence keeps surfacing as
    ``MaxStepsExceeded`` rather than as a new error type.
    """
    logger.debug(
        "compiling workflow acceptance predicate",
        extra={"event": _COMPILE_EVENT, "predicate_kind": spec.kind},
    )
    if spec.kind == _KIND_CONTAINS:
        return _compile_contains(spec)
    if spec.kind == _KIND_JSON_FIELD:
        return _compile_json_field(spec)
    return _compile_regex(spec)
