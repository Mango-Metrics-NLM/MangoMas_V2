"""Tests for the declarative acceptance-predicate compiler."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from mangomas.core import AgentResponse
from mangomas.errors import ConfigError
from mangomas.workflow.predicate import PredicateSpec, compile_predicate
from tests.constants import WORKFLOW_LOOP_SENTINEL


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
