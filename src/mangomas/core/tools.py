"""Tool protocol, registry type alias, call parsing, and prompt construction."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol, TypeAlias, runtime_checkable

from pydantic import BaseModel, Field

from mangomas.errors import LLMBadResponse
from mangomas.registry import Registry

logger = logging.getLogger(__name__)

# ── Tool specification models ─────────────────────────────────────────────────


class ToolSpec(BaseModel):
    """Declarative description of a tool exposed to the LLM."""

    name: str
    description: str
    parameters_schema: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    """Parsed structured tool-call emitted by the LLM."""

    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """Result returned from executing a tool."""

    tool: str
    output: str
    error: str | None = None


# ── Tool protocol ─────────────────────────────────────────────────────────────


@runtime_checkable
class Tool(Protocol):
    """Minimal contract every registered tool must satisfy."""

    @property
    def name(self) -> str:
        """Unique identifier for this tool."""
        ...

    @property
    def spec(self) -> ToolSpec:
        """Declarative specification (used to build the LLM system prompt)."""
        ...

    async def execute(self, arguments: dict[str, Any]) -> str:
        """Run the tool with *arguments* and return a string result."""
        ...


# ── Registry type alias ───────────────────────────────────────────────────────

ToolRegistry: TypeAlias = Registry[Tool]

# ── Prompt templates ──────────────────────────────────────────────────────────

_DEFAULT_TOOL_PROMPT_TEMPLATE: str = (
    "You have access to the following tools. "
    "To invoke a tool respond with a JSON block:\n\n"
    "```json\n"
    '{{"tool": "<tool_name>", "arguments": {{<args>}}}}\n'
    "```\n\n"
    "Available tools:\n"
    "{tools_list}\n\n"
    "If you do not need a tool, respond normally."
)

_DEFAULT_STRUCTURED_PROMPT_TEMPLATE: str = (
    "Respond ONLY with a valid JSON object matching this schema:\n\n"
    "{schema}\n\n"
    "Do not include markdown fences, explanations, or any text outside the JSON object."
)

# ── JSON fence pattern ────────────────────────────────────────────────────────

_JSON_FENCE_RE: re.Pattern[str] = re.compile(r"```(?:json)?\s*([\[{].*?)\s*```", re.DOTALL)


# ── Prompt builders ───────────────────────────────────────────────────────────


def build_tool_system_prompt(specs: list[ToolSpec], *, template: str | None = None) -> str:
    """Build an LLM system prompt listing available tools.

    Parameters
    ----------
    specs:
        Tool specifications to include in the prompt.
    template:
        Optional override for the default template.  Must contain a
        ``{tools_list}`` placeholder.
    """
    tools_list = "\n".join(f"- {s.name}: {s.description}" for s in specs)
    tpl = template if template is not None else _DEFAULT_TOOL_PROMPT_TEMPLATE
    return tpl.format(tools_list=tools_list)


def build_structured_prompt(schema: dict[str, Any], *, template: str | None = None) -> str:
    """Build an LLM system prompt instructing structured JSON output.

    Parameters
    ----------
    schema:
        JSON Schema dict (e.g. from ``Model.model_json_schema()``).
    template:
        Optional override.  Must contain a ``{schema}`` placeholder.
    """
    tpl = template if template is not None else _DEFAULT_STRUCTURED_PROMPT_TEMPLATE
    return tpl.format(schema=json.dumps(schema, indent=2))


# ── Tool call parser ──────────────────────────────────────────────────────────


class ToolCallParser:
    """Parse LLM text output into a :class:`ToolCall`, or ``None`` for plain prose.

    Returns ``None`` when no JSON-like structure is detected.
    Raises :class:`~mangomas.errors.LLMBadResponse` only when a JSON-looking
    block is found but is structurally invalid or missing the ``"tool"`` key.
    This prevents false positives on any LLM prose that happens to contain
    curly braces.
    """

    def parse(self, text: str) -> ToolCall | None:
        """Return a :class:`ToolCall` if *text* contains a tool invocation, else ``None``."""
        # Priority 1: fenced ```json ... ``` block.
        match = _JSON_FENCE_RE.search(text)
        if match:
            raw = match.group(1)
            logger.debug("ToolCallParser: fenced JSON block detected")
            return self._parse_raw(raw)

        # Priority 2: bare {...} object containing a "tool" key.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and start < end:
            raw = text[start : end + 1]
            if '"tool"' in raw or "'tool'" in raw:
                logger.debug("ToolCallParser: bare JSON object detected")
                return self._parse_raw(raw)

        return None

    def _parse_raw(self, raw: str) -> ToolCall:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMBadResponse(
                "Tool call JSON is syntactically invalid.",
                detail=raw[:200],
            ) from exc

        if not isinstance(data, dict):
            raise LLMBadResponse(
                "Tool call JSON must be a JSON object.",
                detail=raw[:200],
            )
        if "tool" not in data:
            raise LLMBadResponse(
                "Tool call JSON is missing the required 'tool' key.",
                detail=raw[:200],
            )
        try:
            return ToolCall.model_validate(data)
        except Exception as exc:
            raise LLMBadResponse(
                "Tool call JSON does not match expected shape.",
                detail=str(exc)[:200],
            ) from exc


# Keep a module-level default instance for convenience.
_default_parser = ToolCallParser()


def parse_tool_call(text: str) -> ToolCall | None:
    """Module-level convenience wrapper around :class:`ToolCallParser`."""
    return _default_parser.parse(text)
