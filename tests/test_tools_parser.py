"""Tests for ToolCallParser error branches and prompt builders."""

from __future__ import annotations

import pytest

from mangomas.core.tools import (
    ToolCallParser,
    ToolSpec,
    build_structured_prompt,
    build_tool_system_prompt,
)
from mangomas.errors import LLMBadResponse

# ── ToolCallParser — error paths ─────────────────────────────────────────────


def test_parser_returns_none_for_plain_prose() -> None:
    parser = ToolCallParser()
    assert parser.parse("Hello, how can I help you?") is None


def test_parser_fenced_invalid_json_raises_llm_bad_response() -> None:
    parser = ToolCallParser()
    with pytest.raises(LLMBadResponse, match="syntactically invalid"):
        parser.parse('```json\n{"tool": bad_value}\n```')


def test_parser_fenced_non_object_json_raises_llm_bad_response() -> None:
    parser = ToolCallParser()
    with pytest.raises(LLMBadResponse, match="JSON object"):
        parser.parse('```json\n["list", "not", "object"]\n```')


def test_parser_fenced_missing_tool_key_raises_llm_bad_response() -> None:
    parser = ToolCallParser()
    with pytest.raises(LLMBadResponse, match="'tool' key"):
        parser.parse('```json\n{"action": "do_thing"}\n```')


def test_parser_fenced_wrong_shape_raises_llm_bad_response() -> None:
    parser = ToolCallParser()
    with pytest.raises(LLMBadResponse, match="expected shape"):
        parser.parse('```json\n{"tool": "search", "arguments": "bad"}\n```')


def test_parser_bare_json_invalid_raises_llm_bad_response() -> None:
    parser = ToolCallParser()
    with pytest.raises(LLMBadResponse, match="syntactically invalid"):
        parser.parse('{"tool": bad}')


def test_parser_valid_fenced_json_returns_tool_call() -> None:
    parser = ToolCallParser()
    result = parser.parse('```json\n{"tool": "my_tool", "arguments": {"k": "v"}}\n```')
    assert result is not None
    assert result.tool == "my_tool"
    assert result.arguments == {"k": "v"}


def test_parser_bare_json_valid_returns_tool_call() -> None:
    parser = ToolCallParser()
    result = parser.parse('some text {"tool": "bare_tool"} more text')
    assert result is not None
    assert result.tool == "bare_tool"


# ── Prompt builders ───────────────────────────────────────────────────────────


def test_build_tool_system_prompt_contains_tool_names() -> None:
    specs = [
        ToolSpec(name="search", description="Search the web"),
        ToolSpec(name="calc", description="Calculate math"),
    ]
    prompt = build_tool_system_prompt(specs)
    assert "search" in prompt
    assert "calc" in prompt


def test_build_tool_system_prompt_custom_template() -> None:
    specs = [ToolSpec(name="t", description="d")]
    prompt = build_tool_system_prompt(specs, template="TOOLS: {tools_list}")
    assert prompt == "TOOLS: - t: d"


def test_build_structured_prompt_contains_schema_keys() -> None:
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    prompt = build_structured_prompt(schema)
    assert '"name"' in prompt
    assert '"type"' in prompt


def test_build_structured_prompt_custom_template() -> None:
    schema = {"type": "object"}
    prompt = build_structured_prompt(schema, template="SCHEMA:{schema}")
    assert "SCHEMA:" in prompt
    assert '"type"' in prompt
