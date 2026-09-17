"""Tool protocol, registry type alias, call parsing, and prompt construction.

Also the **permanent re-export facade** (ADR-0019 / spec-0015 R4) for the
structured-output surface that moved to :mod:`mangomas.core.structured`:
``build_structured_prompt``, ``parse_or_recover``, and the private
``_DEFAULT_STRUCTURED_PROMPT_TEMPLATE`` / ``_ERROR_DETAIL_TRUNCATE`` /
``_extract_json_span`` names stay importable from this module forever and
resolve to the same objects — pinned by ``tests/test_import_compat.py``.
"""

from __future__ import annotations

import json
import logging
import re
from enum import StrEnum
from typing import Any, Protocol, TypeAlias, runtime_checkable

from pydantic import BaseModel, Field

from mangomas.core.structured import (
    _DEFAULT_STRUCTURED_PROMPT_TEMPLATE as _DEFAULT_STRUCTURED_PROMPT_TEMPLATE,
)
from mangomas.core.structured import (
    _ERROR_DETAIL_TRUNCATE as _ERROR_DETAIL_TRUNCATE,
)
from mangomas.core.structured import (
    _extract_json_span as _extract_json_span,
)
from mangomas.core.structured import (
    build_structured_prompt as build_structured_prompt,
)
from mangomas.core.structured import (
    parse_or_recover as parse_or_recover,
)
from mangomas.errors import LLMBadResponse
from mangomas.registry import Registry

logger = logging.getLogger(__name__)

# ── Tool specification models ─────────────────────────────────────────────────


class ToolEffects(StrEnum):
    """What executing a tool does to the world outside this process.

    ``UNDECLARED`` is the default, and is the honest one. The obvious
    alternative — ``read_only: bool = True`` — would label every existing tool
    read-only on the strength of its author never having considered the
    question, which is a field that lies. A consumer that must decide (a
    broker, a policy check) should treat ``UNDECLARED`` as ``MUTATES`` and fail
    closed; the difference is that the record then says "nobody declared"
    rather than "declared safe".

    Advisory metadata, not enforcement: nothing in this repository gates on it
    today. It exists so that the day a write-capable tool is registered, the
    vocabulary to distinguish it already exists on the contract rather than
    needing to be added under pressure (ADR-0033).
    """

    UNDECLARED = "undeclared"
    READ_ONLY = "read_only"
    MUTATES = "mutates"


class ToolSpec(BaseModel):
    """Declarative description of a tool exposed to the LLM."""

    name: str
    description: str
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    # Additive with a default, so every existing construction and every
    # third-party tool keeps working untouched.
    effects: ToolEffects = ToolEffects.UNDECLARED


class ToolCall(BaseModel):
    """Parsed structured tool-call emitted by the LLM."""

    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


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
        # Only fenced blocks raise on malformed JSON; bare detection must be
        # tolerant so that prose containing curly braces never crashes the agent.
        raw_span = _extract_json_span(text)
        if raw_span is not None and ('"tool"' in raw_span or "'tool'" in raw_span):
            logger.debug("ToolCallParser: bare JSON object detected")
            try:
                return self._parse_raw(raw_span)
            except LLMBadResponse:
                logger.debug(
                    "ToolCallParser: bare JSON object failed validation; treating as prose"
                )
                return None

        return None

    def _parse_raw(self, raw: str) -> ToolCall:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMBadResponse(
                "Tool call JSON is syntactically invalid.",
                detail=raw[:_ERROR_DETAIL_TRUNCATE],
            ) from exc

        if not isinstance(data, dict):
            raise LLMBadResponse(
                "Tool call JSON must be a JSON object.",
                detail=raw[:_ERROR_DETAIL_TRUNCATE],
            )
        if "tool" not in data:
            raise LLMBadResponse(
                "Tool call JSON is missing the required 'tool' key.",
                detail=raw[:_ERROR_DETAIL_TRUNCATE],
            )
        try:
            return ToolCall.model_validate(data)
        except Exception as exc:
            raise LLMBadResponse(
                "Tool call JSON does not match expected shape.",
                detail=str(exc)[:_ERROR_DETAIL_TRUNCATE],
            ) from exc


def parse_tool_call(text: str) -> ToolCall | None:
    """Module-level convenience wrapper around :class:`ToolCallParser`.

    Kept as a live delegation (not deleted with the spec-0015 R4 dead code):
    ``tests/test_tools.py`` exercises it, and the parser is stateless, so
    constructing one per call is behaviourally identical to the removed
    module-level ``_default_parser`` instance.
    """
    return ToolCallParser().parse(text)
