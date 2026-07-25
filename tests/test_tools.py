"""Tests for tool protocol, tool call parser, prompt builders, and registry."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest
from pydantic import BaseModel

from mangomas.core.tools import (
    ToolCall,
    ToolCallParser,
    ToolSpec,
    build_structured_prompt,
    build_tool_system_prompt,
    parse_or_recover,
    parse_tool_call,
)
from mangomas.errors import LLMBadResponse
from tests.constants import DEFAULT_TOOL_NAME
from tests.fakes import FakeTool


class _DemoModel(BaseModel):
    """Tiny model used to exercise ``parse_or_recover`` without coupling to real agents."""

    name: str
    value: int


_HypothesisDecorator = Callable[[Callable[..., Any]], Callable[..., Any]]
_hypothesis = pytest.importorskip("hypothesis")
given = cast(Callable[..., _HypothesisDecorator], _hypothesis.given)
settings = cast(Callable[..., _HypothesisDecorator], _hypothesis.settings)
st: Any = pytest.importorskip("hypothesis.strategies")

# ── ToolSpec / ToolCall / ToolResult models ───────────────────────────────────


def test_tool_spec_defaults() -> None:
    spec = ToolSpec(name="search", description="Web search")
    assert spec.parameters_schema == {}


def test_tool_call_defaults() -> None:
    call = ToolCall(tool="noop")
    assert call.arguments == {}


# ── FakeTool satisfies Tool protocol ─────────────────────────────────────────


def test_fake_tool_spec_name_matches() -> None:
    tool = FakeTool(name=DEFAULT_TOOL_NAME)
    assert tool.spec.name == DEFAULT_TOOL_NAME


async def test_fake_tool_execute_returns_result_and_records_call() -> None:
    tool = FakeTool(result="pong")
    out = await tool.execute({"arg": "ping"})
    assert out == "pong"
    assert tool.calls == [{"arg": "ping"}]


# ── build_tool_system_prompt ──────────────────────────────────────────────────


def test_build_tool_system_prompt_contains_tool_name() -> None:
    specs = [ToolSpec(name="search", description="Web search")]
    prompt = build_tool_system_prompt(specs)
    assert "search" in prompt
    assert "Web search" in prompt


def test_build_tool_system_prompt_custom_template() -> None:
    specs = [ToolSpec(name="calc", description="Calculator")]
    prompt = build_tool_system_prompt(specs, template="TOOLS: {tools_list}")
    assert prompt == "TOOLS: - calc: Calculator"


def test_build_tool_system_prompt_multiple_tools() -> None:
    specs = [
        ToolSpec(name="a", description="Alpha"),
        ToolSpec(name="b", description="Beta"),
    ]
    prompt = build_tool_system_prompt(specs)
    assert "- a: Alpha" in prompt
    assert "- b: Beta" in prompt


# ── build_structured_prompt ───────────────────────────────────────────────────


def test_build_structured_prompt_contains_schema() -> None:
    schema = {"type": "object", "properties": {"result": {"type": "string"}}}
    prompt = build_structured_prompt(schema)
    assert '"result"' in prompt


def test_build_structured_prompt_custom_template() -> None:
    prompt = build_structured_prompt({"a": 1}, template="SCHEMA: {schema}")
    assert prompt.startswith("SCHEMA: ")
    assert '"a"' in prompt


# ── ToolCallParser ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        '```json\n{"tool": "search", "arguments": {"q": "cats"}}\n```',
        '```\n{"tool": "search", "arguments": {}}\n```',
    ],
)
def test_parser_fenced_json_roundtrip(text: str) -> None:
    parser = ToolCallParser()
    call = parser.parse(text)
    assert call is not None
    assert call.tool == "search"


def test_parser_bare_json_with_tool_key() -> None:
    text = '{"tool": "calc", "arguments": {"x": 1}}'
    call = parse_tool_call(text)
    assert call is not None
    assert call.tool == "calc"
    assert call.arguments == {"x": 1}


def test_parser_plain_prose_returns_none() -> None:
    assert parse_tool_call("I can help you with that!") is None


def test_parser_json_without_tool_key_returns_none() -> None:
    # Bare object but no "tool" key → not treated as a tool call.
    assert parse_tool_call('{"result": "hello"}') is None


def test_parser_fenced_invalid_json_raises_bad_response() -> None:
    parser = ToolCallParser()
    with pytest.raises(LLMBadResponse):
        parser.parse("```json\n{broken json\n```")


def test_parser_fenced_missing_tool_key_raises_bad_response() -> None:
    parser = ToolCallParser()
    with pytest.raises(LLMBadResponse):
        parser.parse('```json\n{"name": "calc"}\n```')


def test_parser_arguments_optional() -> None:
    call = parse_tool_call('```json\n{"tool": "noop"}\n```')
    assert call is not None
    assert call.arguments == {}


# ── Hypothesis fuzz: parser never raises unexpected exceptions ────────────────


@settings(max_examples=500)
@given(st.text())
def test_tool_call_parser_never_raises_unexpected(text: str) -> None:
    """The parser must only raise LLMBadResponse or return None — never anything else."""
    parser = ToolCallParser()
    try:
        parser.parse(text)
    except LLMBadResponse:
        pass
    except Exception as exc:
        pytest.fail(f"Unexpected exception {type(exc).__name__}: {exc}")


# ── json round-trip: serialisation stability ──────────────────────────────────


def test_tool_call_json_roundtrip() -> None:
    original = ToolCall(tool="echo", arguments={"msg": "hi"})
    restored = ToolCall.model_validate_json(original.model_dump_json())
    assert restored == original


def test_tool_spec_json_roundtrip() -> None:
    original = ToolSpec(
        name="fn",
        description="A function",
        parameters_schema={"type": "object"},
    )
    restored = ToolSpec.model_validate_json(original.model_dump_json())
    assert restored == original


# ── parse_or_recover: structured-output recovery ──────────────────────────────


def test_parse_or_recover_clean_json() -> None:
    """Well-formed JSON validates directly against the model."""
    parsed = parse_or_recover('{"name": "x", "value": 1}', _DemoModel)
    assert parsed is not None
    assert parsed.name == "x"
    assert parsed.value == 1


def test_parse_or_recover_with_markdown_fence() -> None:
    """Strips chatter outside the first ``{`` and last ``}`` then re-parses."""
    wrapped = '```json\n{"name": "y", "value": 2}\n```'
    parsed = parse_or_recover(wrapped, _DemoModel)
    assert parsed is not None
    assert parsed.value == 2


def test_parse_or_recover_with_leading_chatter() -> None:
    """LLM prose preamble before JSON is tolerated."""
    raw = 'Sure, here is the result: {"name": "z", "value": 3} hope that helps.'
    parsed = parse_or_recover(raw, _DemoModel)
    assert parsed is not None
    assert parsed.name == "z"


def test_parse_or_recover_no_braces_returns_none() -> None:
    """Plain prose with no JSON object returns None."""
    assert parse_or_recover("no json here", _DemoModel) is None


def test_parse_or_recover_malformed_json_returns_none() -> None:
    """Salvage path also fails for syntactically invalid JSON."""
    assert parse_or_recover("{not valid json}", _DemoModel) is None


def test_parse_or_recover_schema_mismatch_returns_none() -> None:
    """JSON parses but does not satisfy the model schema."""
    assert parse_or_recover('{"wrong": "field"}', _DemoModel) is None


def test_parse_or_recover_inverted_braces_returns_none() -> None:
    """``}`` appearing before ``{`` is not a valid recovery target."""
    assert parse_or_recover("} oops {", _DemoModel) is None
