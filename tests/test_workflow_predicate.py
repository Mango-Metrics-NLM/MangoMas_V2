"""Tests for the declarative acceptance-predicate compiler."""

from __future__ import annotations

import asyncio
import json
from typing import Literal

import pytest
from pydantic import ValidationError

from mangomas.agents.reviewer import ReviewResult
from mangomas.core import AgentResponse
from mangomas.core.loop import AcceptanceFn
from mangomas.errors import ConfigError
from mangomas.workflow.predicate import PredicateSpec, compile_predicate
from tests.constants import (
    PASSED_NEEDLE_COMPACT,
    PASSED_NEEDLE_QUOTELESS,
    PASSED_NEEDLE_SPACED,
    PREDICATE_INT_FIELD,
    PREDICATE_INT_VALUE,
    PREDICATE_INVALID_JSON,
    PREDICATE_JSON_ARRAY,
    REVIEW_APPROVED_FEEDBACK,
    REVIEW_BOOL_THRESHOLD,
    REVIEW_FEEDBACK_FIELD,
    REVIEW_NESTED_PASSED_PATH,
    REVIEW_PASSED_FIELD,
    REVIEW_REJECTED_FEEDBACK,
    REVIEW_REJECTED_SUGGESTION,
    REVIEW_SCORE_ABOVE,
    REVIEW_SCORE_AT_LEAST,
    REVIEW_SCORE_AT_MOST,
    REVIEW_SCORE_BELOW,
    REVIEW_SCORE_FIELD,
    REVIEW_SCORE_LOW,
    REVIEW_SUGGESTIONS_FIELD,
    REVIEW_WRAPPER_FIELD,
    WORKFLOW_LOOP_SENTINEL,
    WORKFLOW_PREDICATE_KINDS,
)

PredicateKind = Literal["contains", "regex", "json_field"]

JSON_FIELD_KIND: PredicateKind = "json_field"
PREDICATE_VALUE_FIELD = "value"
PREDICATE_FIELD_FIELD = "field"


def _resp(content: str) -> AgentResponse:
    return AgentResponse(content=content, agent="x")


def test_contains_case_insensitive_by_default() -> None:
    fn = compile_predicate(PredicateSpec(kind="contains", value=WORKFLOW_LOOP_SENTINEL))
    assert fn(_resp("all done")) is True
    assert fn(_resp("not yet")) is False


def test_contains_case_sensitive() -> None:
    fn = compile_predicate(
        PredicateSpec(kind="contains", value=WORKFLOW_LOOP_SENTINEL, case_sensitive=True)
    )
    assert fn(_resp("all DONE")) is True
    assert fn(_resp("all done")) is False


def test_regex_search_with_flags() -> None:
    fn = compile_predicate(PredicateSpec(kind="regex", value="^ok", flags=["ignorecase"]))
    assert fn(_resp("OK great")) is True
    assert fn(_resp("great OK")) is False  # anchored ^ + search


def test_regex_multiline_and_dotall_flags_resolve() -> None:
    fn = compile_predicate(PredicateSpec(kind="regex", value="^done$", flags=["multiline"]))
    assert fn(_resp("intro\ndone\noutro")) is True


def test_unknown_predicate_kind_rejected_at_construction() -> None:
    with pytest.raises(ValidationError):
        PredicateSpec(kind="bogus", value="x")  # type: ignore[arg-type]


def test_bad_regex_flag_raises_config_error() -> None:
    with pytest.raises(ConfigError, match="unknown regex flag"):
        compile_predicate(PredicateSpec(kind="regex", value="x", flags=["nope"]))


def test_invalid_regex_pattern_raises_config_error() -> None:
    with pytest.raises(ConfigError, match="invalid regex pattern"):
        compile_predicate(PredicateSpec(kind="regex", value="("))


def test_compiled_predicate_is_sync_callable() -> None:
    fn = compile_predicate(PredicateSpec(kind="contains", value="x"))
    assert callable(fn)
    assert not asyncio.iscoroutinefunction(fn)


# ── spec-0032: structured acceptance predicates ───────────────────────────────
#
# The four tests below are the measured defect, not illustrations of it. Each
# needle/response pair was observed producing the WRONG verdict under `contains`
# before `json_field` existed, so each asserts BOTH directions: the text match is
# wrong, and the structured match is right. Deleting the `contains` half would
# leave a test that passes without proving anything.


def _review_json(
    *,
    passed: bool,
    score: float = REVIEW_SCORE_ABOVE,
    feedback: str = REVIEW_APPROVED_FEEDBACK,
    suggestions: list[str] | None = None,
    indent: int | None = None,
) -> str:
    """Serialise a real ``ReviewResult`` — compact by default, pretty with *indent*."""
    review = ReviewResult(
        passed=passed,
        score=score,
        feedback=feedback,
        suggestions=suggestions if suggestions is not None else [],
    )
    if indent is None:
        return review.model_dump_json()
    return json.dumps(json.loads(review.model_dump_json()), indent=indent)


def _passed_is(expected: bool) -> AcceptanceFn:
    return compile_predicate(
        PredicateSpec(kind=JSON_FIELD_KIND, field=REVIEW_PASSED_FIELD, equals=expected)
    )


def test_review_fields_match_the_real_schema() -> None:
    """Pin the fixtures to ``ReviewResult`` so a rename fails here, not silently."""
    assert set(ReviewResult.model_fields) == {
        REVIEW_PASSED_FIELD,
        REVIEW_SCORE_FIELD,
        REVIEW_FEEDBACK_FIELD,
        REVIEW_SUGGESTIONS_FIELD,
    }


def test_spaced_needle_is_a_false_negative_on_compact_json() -> None:
    """Measured mode 1: the natural spelling never matches ``model_dump_json()``.

    A human writes ``"passed": true``; pydantic emits ``"passed":true``. The loop
    would run to ``max_steps`` and raise ``MaxStepsExceeded`` on an APPROVING review.
    """
    content = _review_json(passed=True)
    assert (
        compile_predicate(PredicateSpec(kind="contains", value=PASSED_NEEDLE_SPACED))(
            _resp(content)
        )
        is False
    )  # the defect
    assert _passed_is(True)(_resp(content)) is True  # the fix


def test_compact_needle_is_a_false_negative_on_pretty_json() -> None:
    """Measured mode 2: a needle tuned to compact output breaks under indent."""
    content = _review_json(passed=True, indent=2)
    assert (
        compile_predicate(PredicateSpec(kind="contains", value=PASSED_NEEDLE_COMPACT))(
            _resp(content)
        )
        is False
    )  # the defect
    assert _passed_is(True)(_resp(content)) is True  # the fix


def test_quoteless_needle_is_a_false_positive_via_feedback() -> None:
    """Measured mode 3: the needle that survives formatting drift matches prose.

    This is the one that accepts a REJECTING review — reached precisely by
    "fixing" the two false negatives above.
    """
    content = _review_json(passed=False, score=REVIEW_SCORE_LOW, feedback=REVIEW_REJECTED_FEEDBACK)
    assert (
        compile_predicate(PredicateSpec(kind="contains", value=PASSED_NEEDLE_QUOTELESS))(
            _resp(content)
        )
        is True
    )  # the defect: accepts a rejection
    assert _passed_is(True)(_resp(content)) is False  # the fix


def test_quoteless_needle_is_a_false_positive_via_suggestions() -> None:
    """Measured mode 4: the same bleed through ``suggestions`` rather than ``feedback``."""
    content = _review_json(
        passed=False,
        score=REVIEW_SCORE_LOW,
        suggestions=[REVIEW_REJECTED_SUGGESTION],
    )
    assert (
        compile_predicate(PredicateSpec(kind="contains", value=PASSED_NEEDLE_QUOTELESS))(
            _resp(content)
        )
        is True
    )  # the defect: accepts a rejection
    assert _passed_is(True)(_resp(content)) is False  # the fix


def test_equals_false_matches_a_rejecting_review() -> None:
    """``equals`` is given when it is ``False`` — a truthiness test would drop it."""
    assert _passed_is(False)(_resp(_review_json(passed=False, score=REVIEW_SCORE_LOW))) is True


def test_at_least_straddles_its_threshold() -> None:
    fn = compile_predicate(
        PredicateSpec(
            kind=JSON_FIELD_KIND, field=REVIEW_SCORE_FIELD, at_least=REVIEW_SCORE_AT_LEAST
        )
    )
    assert fn(_resp(_review_json(passed=True, score=REVIEW_SCORE_ABOVE))) is True
    assert fn(_resp(_review_json(passed=True, score=REVIEW_SCORE_BELOW))) is False


def test_at_most_straddles_its_threshold() -> None:
    fn = compile_predicate(
        PredicateSpec(kind=JSON_FIELD_KIND, field=REVIEW_SCORE_FIELD, at_most=REVIEW_SCORE_AT_MOST)
    )
    assert fn(_resp(_review_json(passed=False, score=REVIEW_SCORE_LOW))) is True
    assert fn(_resp(_review_json(passed=True, score=REVIEW_SCORE_ABOVE))) is False


def test_boolean_never_satisfies_a_numeric_threshold() -> None:
    """spec-0032 R4: ``True >= 0.5`` is silently true in Python. It must not be here."""
    fn = compile_predicate(
        PredicateSpec(
            kind=JSON_FIELD_KIND, field=REVIEW_PASSED_FIELD, at_least=REVIEW_BOOL_THRESHOLD
        )
    )
    assert fn(_resp(_review_json(passed=True))) is False


def test_equals_does_not_conflate_bool_and_int_either_way() -> None:
    """``True == 1`` in Python, so bool-ness is compared before equality."""
    as_int = json.dumps({PREDICATE_INT_FIELD: PREDICATE_INT_VALUE})
    as_bool = json.dumps({PREDICATE_INT_FIELD: True})
    wants_true = compile_predicate(
        PredicateSpec(kind=JSON_FIELD_KIND, field=PREDICATE_INT_FIELD, equals=True)
    )
    wants_one = compile_predicate(
        PredicateSpec(kind=JSON_FIELD_KIND, field=PREDICATE_INT_FIELD, equals=PREDICATE_INT_VALUE)
    )
    assert wants_true(_resp(as_int)) is False
    assert wants_one(_resp(as_bool)) is False
    assert wants_true(_resp(as_bool)) is True
    assert wants_one(_resp(as_int)) is True


def test_dotted_path_resolves_through_a_wrapper() -> None:
    nested = json.dumps({REVIEW_WRAPPER_FIELD: json.loads(_review_json(passed=True))})
    fn = compile_predicate(
        PredicateSpec(kind=JSON_FIELD_KIND, field=REVIEW_NESTED_PASSED_PATH, equals=True)
    )
    assert fn(_resp(nested)) is True


@pytest.mark.parametrize(
    "content",
    [
        PREDICATE_INVALID_JSON,
        PREDICATE_JSON_ARRAY,
        "{}",
        json.dumps({REVIEW_WRAPPER_FIELD: REVIEW_APPROVED_FEEDBACK}),
    ],
    ids=["invalid-json", "json-array-not-object", "absent-field", "non-mapping-intermediate"],
)
def test_unresolvable_content_rejects_without_raising(content: str) -> None:
    """R6: the closure is total — no response content makes it raise."""
    assert _passed_is(True)(_resp(content)) is False
    nested = compile_predicate(
        PredicateSpec(kind=JSON_FIELD_KIND, field=REVIEW_NESTED_PASSED_PATH, equals=True)
    )
    assert nested(_resp(content)) is False


# ── Per-kind validation: both directions (specs/TEMPLATE.md) ──────────────────


@pytest.mark.parametrize(
    "kwargs",
    [
        {"field": REVIEW_PASSED_FIELD},
        {"field": REVIEW_PASSED_FIELD, "equals": True, "at_least": REVIEW_SCORE_AT_LEAST},
        {"equals": True},
        {"field": ""},
        {"field": REVIEW_PASSED_FIELD, "equals": True, "value": PASSED_NEEDLE_SPACED},
        {"field": REVIEW_PASSED_FIELD, "equals": True, "case_sensitive": True},
        {"field": REVIEW_PASSED_FIELD, "equals": True, "flags": ["ignorecase"]},
    ],
    ids=[
        "no-comparison",
        "two-comparisons",
        "no-field",
        "empty-field",
        "value-not-applicable",
        "case-sensitive-not-applicable",
        "flags-not-applicable",
    ],
)
def test_invalid_json_field_spec_is_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PredicateSpec(kind=JSON_FIELD_KIND, **kwargs)  # type: ignore[arg-type]


def test_valid_json_field_spec_constructs() -> None:
    """The rejecting direction above is only meaningful if this one passes."""
    assert PredicateSpec(kind=JSON_FIELD_KIND, field=REVIEW_PASSED_FIELD, equals=True)


@pytest.mark.parametrize("kind", ["contains", "regex"])
@pytest.mark.parametrize("value", [None, ""], ids=["omitted", "empty"])
def test_text_kind_still_requires_a_value(kind: PredicateKind, value: str | None) -> None:
    """Relaxing ``value`` to optional must not widen the schema for text kinds."""
    with pytest.raises(ValidationError):
        PredicateSpec(kind=kind, value=value)


@pytest.mark.parametrize("name", ["field", "equals", "at_least", "at_most"])
def test_text_kind_rejects_json_field_only_fields(name: str) -> None:
    with pytest.raises(ValidationError):
        PredicateSpec(kind="contains", value=WORKFLOW_LOOP_SENTINEL, **{name: True})  # type: ignore[arg-type]


def test_every_declared_kind_compiles() -> None:
    """Pin the kind vocabulary so a new kind cannot be added without a compiler arm."""
    assert set(WORKFLOW_PREDICATE_KINDS) == set(
        PredicateSpec.model_fields["kind"].annotation.__args__  # type: ignore[union-attr]
    )


def test_non_numeric_field_never_satisfies_a_threshold() -> None:
    """A string where a number is expected rejects rather than raising."""
    fn = compile_predicate(
        PredicateSpec(
            kind=JSON_FIELD_KIND, field=REVIEW_FEEDBACK_FIELD, at_least=REVIEW_SCORE_AT_LEAST
        )
    )
    assert fn(_resp(_review_json(passed=True))) is False


# ── Compile-time guards for specs that bypassed validation ────────────────────
#
# `model_construct` skips the model validator, which is the documented way to
# build an unvalidated spec. These prove the claims the compile-time guards make
# in their docstrings: no broken spec may ever yield a usable closure. Without
# them those guards are unexecuted prose.


@pytest.mark.parametrize("kind", ["contains", "regex"])
def test_unvalidated_text_spec_without_a_value_refuses_to_compile(kind: PredicateKind) -> None:
    """An empty needle would compile to a closure that accepts everything."""
    with pytest.raises(ConfigError, match=f"requires a non-empty '{PREDICATE_VALUE_FIELD}'"):
        compile_predicate(PredicateSpec.model_construct(kind=kind, value=None))


def test_unvalidated_json_field_spec_without_a_field_refuses_to_compile() -> None:
    with pytest.raises(ConfigError, match=f"requires a non-empty '{PREDICATE_FIELD_FIELD}'"):
        compile_predicate(
            PredicateSpec.model_construct(kind=JSON_FIELD_KIND, field=None, equals=True)
        )


@pytest.mark.parametrize(
    "spec",
    [
        PredicateSpec.model_construct(kind=JSON_FIELD_KIND, field=REVIEW_PASSED_FIELD),
        PredicateSpec.model_construct(
            kind=JSON_FIELD_KIND,
            field=REVIEW_PASSED_FIELD,
            equals=True,
            at_least=REVIEW_SCORE_AT_LEAST,
        ),
    ],
    ids=["none", "two"],
)
def test_unvalidated_json_field_spec_needs_exactly_one_comparison(spec: PredicateSpec) -> None:
    with pytest.raises(ConfigError, match="requires exactly one of"):
        compile_predicate(spec)
