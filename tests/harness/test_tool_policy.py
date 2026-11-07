"""Unit tests for ``mangomas.harness.tool_policy`` and ``ToolAgent`` fail-closed policy (Ring 1)."""

from __future__ import annotations

import json

import pytest

from mangomas.agents.tool_agent import ToolAgent
from mangomas.core.agent import AgentContext, AgentRequest, Message
from mangomas.core.tools import ToolEffects, ToolRegistry, ToolSpec
from mangomas.errors import ToolExecutionError
from mangomas.harness.tool_policy import (
    DEFAULT_MAX_ARGUMENT_COUNT,
    DEFAULT_MAX_STRING_ARGUMENT_CHARS,
    enforce_tool_invocation_policy,
    validate_tool_arguments,
)
from mangomas.registry import Registry
from tests.constants import DEFAULT_TOOL_NAME
from tests.fakes import FakeLLM, FakeTool


def test_read_only_tool_passes_policy() -> None:
    spec = ToolSpec(
        name="retrieve",
        description="Read-only search",
        effects=ToolEffects.READ_ONLY,
        parameters_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer"},
                "strict": {"type": "boolean"},
                "threshold": {"type": "number"},
                "tags": {"type": "array"},
                "filters": {"type": "object"},
                "untyped": {},
                "custom": {"type": "custom_type"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    )
    enforce_tool_invocation_policy(
        spec,
        {
            "query": "mango",
            "top_k": 5,
            "strict": True,
            "threshold": 0.5,
            "tags": ["a"],
            "filters": {"k": "v"},
            "untyped": 123,
            "custom": "ok",
        },
    )


@pytest.mark.parametrize("effect", [ToolEffects.UNDECLARED, ToolEffects.MUTATES])
def test_non_read_only_tool_is_blocked_by_default(effect: ToolEffects) -> None:
    spec = ToolSpec(name="write_file", description="Mutates disk", effects=effect)
    with pytest.raises(ToolExecutionError, match="blocked by the fail-closed tool policy"):
        enforce_tool_invocation_policy(spec, {})


@pytest.mark.parametrize("effect", [ToolEffects.UNDECLARED, ToolEffects.MUTATES])
def test_allowlisted_mutating_tool_is_permitted(effect: ToolEffects) -> None:
    spec = ToolSpec(name="write_file", description="Mutates disk", effects=effect)
    enforce_tool_invocation_policy(spec, {}, allowed_mutating_tools={"write_file"})


def test_validate_tool_arguments_rejects_excessive_argument_count() -> None:
    spec = ToolSpec(name="retrieve", description="Search", effects=ToolEffects.READ_ONLY)
    too_many = {f"k{i}": i for i in range(DEFAULT_MAX_ARGUMENT_COUNT + 1)}
    with pytest.raises(ToolExecutionError, match="maximum allowed is"):
        validate_tool_arguments(spec, too_many)


def test_validate_tool_arguments_rejects_oversized_string() -> None:
    spec = ToolSpec(name="retrieve", description="Search", effects=ToolEffects.READ_ONLY)
    huge = {"query": "x" * (DEFAULT_MAX_STRING_ARGUMENT_CHARS + 1)}
    with pytest.raises(ToolExecutionError, match="exceeds maximum length"):
        validate_tool_arguments(spec, huge)


def test_validate_tool_arguments_rejects_missing_required_key() -> None:
    spec = ToolSpec(
        name="retrieve",
        description="Search",
        effects=ToolEffects.READ_ONLY,
        parameters_schema={"type": "object", "required": ["query"]},
    )
    with pytest.raises(ToolExecutionError, match="missing required argument"):
        validate_tool_arguments(spec, {})


def test_validate_tool_arguments_rejects_unknown_keys_when_additional_properties_false() -> None:
    spec = ToolSpec(
        name="retrieve",
        description="Search",
        effects=ToolEffects.READ_ONLY,
        parameters_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    with pytest.raises(ToolExecutionError, match="unexpected argument"):
        validate_tool_arguments(spec, {"query": "ok", "rogue": 1})


@pytest.mark.parametrize(
    ("expected_type", "bad_value"),
    [
        ("string", 123),
        ("boolean", 1),
        ("integer", True),
        ("integer", "5"),
        ("number", False),
        ("number", "3.14"),
        ("array", "not-a-list"),
        ("object", ["not", "a", "mapping"]),
    ],
)
def test_validate_tool_arguments_rejects_wrong_primitive_type(
    expected_type: str,
    bad_value: object,
) -> None:
    spec = ToolSpec(
        name="retrieve",
        description="Search",
        effects=ToolEffects.READ_ONLY,
        parameters_schema={
            "type": "object",
            "properties": {"field": {"type": expected_type}},
        },
    )
    with pytest.raises(ToolExecutionError, match=f"must be of type {expected_type!r}"):
        validate_tool_arguments(spec, {"field": bad_value})


def test_validate_tool_arguments_skips_non_mapping_property_entry() -> None:
    spec = ToolSpec(
        name="retrieve",
        description="Search",
        effects=ToolEffects.READ_ONLY,
        parameters_schema={"type": "object", "properties": {"field": "not-a-dict"}},
    )
    validate_tool_arguments(spec, {"field": 123})


def test_validate_tool_arguments_returns_when_properties_absent() -> None:
    spec = ToolSpec(
        name="retrieve",
        description="Search",
        effects=ToolEffects.READ_ONLY,
        parameters_schema={"type": "object", "required": ["query"]},
    )
    validate_tool_arguments(spec, {"query": "mango"})


async def test_tool_agent_blocks_undeclared_tool_by_default() -> None:
    tool_json = json.dumps({"tool": DEFAULT_TOOL_NAME, "arguments": {}})
    llm = FakeLLM(reply=f"```json\n{tool_json}\n```")
    undeclared_tool = FakeTool(name=DEFAULT_TOOL_NAME, effects=ToolEffects.UNDECLARED)
    reg: ToolRegistry = Registry("tool")
    reg.register(DEFAULT_TOOL_NAME, undeclared_tool)
    agent = ToolAgent()
    ctx = AgentContext(llm=llm, repo=None, tools=reg)
    req = AgentRequest(messages=[Message(role="user", content="go")])
    with pytest.raises(ToolExecutionError, match="fail-closed tool policy"):
        await agent.handle(req, ctx)
    assert undeclared_tool.calls == []


async def test_tool_agent_allows_explicitly_granted_mutating_tool() -> None:
    tool_json = json.dumps({"tool": DEFAULT_TOOL_NAME, "arguments": {"x": 1}})
    llm = FakeLLM(replies=[f"```json\n{tool_json}\n```", "Done."])
    mutating_tool = FakeTool(name=DEFAULT_TOOL_NAME, effects=ToolEffects.MUTATES)
    reg: ToolRegistry = Registry("tool")
    reg.register(DEFAULT_TOOL_NAME, mutating_tool)
    agent = ToolAgent(allowed_mutating_tools=frozenset({DEFAULT_TOOL_NAME}))
    ctx = AgentContext(llm=llm, repo=None, tools=reg)
    req = AgentRequest(messages=[Message(role="user", content="go")])
    resp = await agent.handle(req, ctx)
    assert resp.content == "Done."
    assert mutating_tool.calls == [{"x": 1}]
