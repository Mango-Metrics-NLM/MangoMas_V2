"""Tests for ``mangomas.core.structured`` (spec-0015 R4).

The relocated surface (``build_structured_prompt``, ``parse_or_recover``) is
already exercised by ``tests/test_tools.py`` *through the facade* — those
tests deliberately stay unmodified as the extraction's behavioural proof.
This file covers what is new in the home module: the shared
``_extract_json_span`` heuristic called directly, and ``parse_llm_json_object``.
"""

from __future__ import annotations

import pytest

from mangomas.core.structured import (
    _ERROR_DETAIL_TRUNCATE,
    _extract_json_span,
    parse_llm_json_object,
)
from mangomas.errors import LLMBadResponse

# ── _extract_json_span ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', '{"a": 1}'),
        ('chatter {"a": 1} trailing', '{"a": 1}'),
        ("no braces at all", None),
        ("only an opening {", None),
        ("only a closing }", None),
        ("} inverted {", None),
    ],
)
def test_extract_json_span(text: str, expected: str | None) -> None:
    assert _extract_json_span(text) == expected


def test_extract_json_span_is_outermost_greedy() -> None:
    """First ``{`` to *last* ``}`` — nested objects come back whole."""
    text = 'x {"a": {"b": 2}} y'
    assert _extract_json_span(text) == '{"a": {"b": 2}}'


# ── parse_llm_json_object ─────────────────────────────────────────────────────


def test_parse_llm_json_object_returns_the_dict() -> None:
    assert parse_llm_json_object('{"score": 0.5}') == {"score": 0.5}


def test_parse_llm_json_object_invalid_json_raises_bad_response() -> None:
    with pytest.raises(LLMBadResponse) as excinfo:
        parse_llm_json_object("not json at all")
    assert excinfo.value.detail == "not json at all"


def test_parse_llm_json_object_non_object_raises_bad_response() -> None:
    """Valid JSON that is not an object (a list, a scalar) is still rejected."""
    with pytest.raises(LLMBadResponse):
        parse_llm_json_object("[1, 2, 3]")
    with pytest.raises(LLMBadResponse):
        parse_llm_json_object('"just a string"')


def test_parse_llm_json_object_truncates_detail_to_the_core_default() -> None:
    """The error ``detail`` never carries the full LLM content."""
    long_garbage = "x" * (_ERROR_DETAIL_TRUNCATE * 2)
    with pytest.raises(LLMBadResponse) as excinfo:
        parse_llm_json_object(long_garbage)
    assert excinfo.value.detail == "x" * _ERROR_DETAIL_TRUNCATE


def test_parse_llm_json_object_honours_a_caller_supplied_truncate() -> None:
    """``detail_truncate`` is a parameter, not a config import (ADR-0019:
    ``core`` stays config-free)."""
    with pytest.raises(LLMBadResponse) as excinfo:
        parse_llm_json_object("garbage input", detail_truncate=7)
    assert excinfo.value.detail == "garbage"


def test_parse_llm_json_object_non_object_detail_is_truncated_too() -> None:
    long_list = "[" + ",".join("1" for _ in range(_ERROR_DETAIL_TRUNCATE)) + "]"
    with pytest.raises(LLMBadResponse) as excinfo:
        parse_llm_json_object(long_list)
    assert len(excinfo.value.detail) == _ERROR_DETAIL_TRUNCATE
