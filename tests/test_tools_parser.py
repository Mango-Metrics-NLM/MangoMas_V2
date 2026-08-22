"""Tests for ToolCallParser error branches.

Prompt-builder coverage lives in ``tests/test_tools.py`` — this file used to
duplicate it, with two function names colliding across the two modules
(pytest does not flag cross-module name collisions, so the redundancy was
invisible). The originals there are strictly broader.
"""

from __future__ import annotations

import pytest

from mangomas.core.tools import ToolCallParser
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


def test_parser_bare_json_invalid_returns_none() -> None:
    """Bare ``{...}`` objects that fail JSON validation must NOT raise.

    Only fenced ``` ```json ``` blocks are treated as authoritative tool calls;
    bare braces in prose are best-effort and should fall through to ``None`` so
    the LLM response is treated as natural text.
    """
    parser = ToolCallParser()
    assert parser.parse('{"tool": bad}') is None


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
