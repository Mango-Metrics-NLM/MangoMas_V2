"""Structured-output helpers: schema prompts and LLM-JSON recovery.

Extracted from :mod:`mangomas.core.tools` (spec-0015 R4 / ADR-0019). That
module remains a **permanent re-export facade** for every name that moved
(``build_structured_prompt``, ``parse_or_recover``, the private prompt
template and truncation constant), so no pre-extraction import path changes;
``tests/test_import_compat.py`` pins the facade by object identity.

``core`` is the innermost layer and imports nothing but ``errors`` and
``registry``, so the error-detail bound here is a core-local constant (and a
``detail_truncate`` parameter on :func:`parse_llm_json_object`) rather than
an import of the settings package — ADR-0019's decision that ``core`` stays
config-free.
"""

from __future__ import annotations

import json
from typing import Any, Final, TypeVar

from pydantic import BaseModel, ValidationError

from mangomas.errors import LLMBadResponse

_StructuredModel = TypeVar("_StructuredModel", bound=BaseModel)

# ── Prompt template ───────────────────────────────────────────────────────────

_DEFAULT_STRUCTURED_PROMPT_TEMPLATE: str = (
    "Respond ONLY with a valid JSON object matching this schema:\n\n"
    "{schema}\n\n"
    "Do not include markdown fences, explanations, or any text outside the JSON object."
)

# Maximum length of the ``detail`` field on errors raised here. Mirrors
# ``mangomas.config.DEFAULT_ERROR_DETAIL_TRUNCATE`` by value rather than by
# import: ``core`` is the innermost layer and deliberately depends on nothing
# but ``errors`` and ``registry``, so importing the settings package here
# would invert the dependency direction for a single integer. The two are
# pinned equal by ``tests/test_tools.py::test_error_detail_truncate_matches_config``
# so they cannot drift apart.
_ERROR_DETAIL_TRUNCATE: Final[int] = 200


# ── Prompt builder ────────────────────────────────────────────────────────────


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


# ── JSON span extraction ──────────────────────────────────────────────────────


def _extract_json_span(text: str) -> str | None:
    """Return the substring from the first ``{`` to the last ``}``, or ``None``.

    The single home for the brace-span heuristic previously duplicated by
    ``ToolCallParser.parse`` and :func:`parse_or_recover` (spec-0015 R4).
    ``None`` when either brace is absent or they are inverted (``} ... {``).
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or start >= end:
        return None
    return text[start : end + 1]


# ── Structured-output recovery / parsing ──────────────────────────────────────


def parse_or_recover(
    content: str,
    model: type[_StructuredModel],
) -> _StructuredModel | None:
    """Validate *content* against *model*, recovering from wrapped JSON.

    Returns the parsed model on success, ``None`` when no salvageable JSON
    object is present. The recovery path strips everything outside the first
    ``{`` and the last ``}`` — this covers the common case of local models
    emitting JSON inside a markdown fence or surrounded by chatter.

    Generic helper for any structured-output agent (planner, reviewer, future
    agents).
    """
    try:
        return model.model_validate_json(content)
    except ValidationError:
        pass

    span = _extract_json_span(content)
    if span is None:
        return None
    try:
        return model.model_validate_json(span)
    except ValidationError:
        return None


def parse_llm_json_object(
    text: str,
    *,
    detail_truncate: int = _ERROR_DETAIL_TRUNCATE,
) -> dict[str, Any]:
    """Parse *text* as a JSON **object**, raising typed errors on failure.

    Shared helper for call sites that require an LLM reply to be a JSON
    object (the LLM-judge scorer, structured-output validation). Raises
    :class:`~mangomas.errors.LLMBadResponse` when *text* is not valid JSON or
    parses to a non-object; the error ``detail`` carries at most
    *detail_truncate* characters of the offending text, never the full LLM
    content (default :data:`_ERROR_DETAIL_TRUNCATE` — core-local, see the
    module docstring).
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMBadResponse(
            "LLM output is not valid JSON.",
            detail=text[:detail_truncate],
        ) from exc
    if not isinstance(data, dict):
        raise LLMBadResponse(
            "LLM output is not a JSON object.",
            detail=text[:detail_truncate],
        )
    return data
