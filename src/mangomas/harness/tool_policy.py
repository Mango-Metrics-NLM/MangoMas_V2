"""Fail-closed policy and argument validation for LLM tool execution (Ring 1).

Enforces two invariants before :class:`~mangomas.agents.ToolAgent` invokes any
registered :class:`~mangomas.core.tools.Tool`:

1. **Tool effects are fail-closed.** :class:`~mangomas.core.tools.ToolEffects`
   defaults to ``UNDECLARED`` so a tool author who never declared an effect is
   not silently assumed safe (ADR-0033). :func:`enforce_tool_invocation_policy`
   refuses any tool whose ``spec.effects`` is not ``READ_ONLY`` unless the
   tool name is explicitly listed in *allowed_mutating_tools*.
2. **Arguments are validated against ``parameters_schema``.** Before
   ``tool.execute(arguments)`` runs, :func:`validate_tool_arguments` verifies
   payload size, required keys, unknown keys (when ``additionalProperties`` is
   ``False``), and JSON-Schema primitive types (handling Python's ``bool``
   subclassing ``int``). Violations raise :class:`~mangomas.errors.ToolExecutionError`.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import TYPE_CHECKING, Any, Final

from mangomas.core.tools import ToolEffects
from mangomas.errors import ToolExecutionError

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core.tools import ToolSpec

DEFAULT_MAX_ARGUMENT_COUNT: Final[int] = 32
DEFAULT_MAX_STRING_ARGUMENT_CHARS: Final[int] = 16_384


def _matches_json_type(value: object, expected_type: str) -> bool:
    """Return whether *value* satisfies the JSON-Schema *expected_type*.

    ``bool`` subclasses ``int`` in Python, so ``integer`` and ``number`` must
    exclude ``bool`` explicitly; ``null`` maps to ``NoneType``; unrecognised
    types pass through so a future ``ToolSpec`` is not silently rejected.
    """
    if expected_type in {"integer", "number"} and isinstance(value, bool):
        return False
    type_map: dict[str, type | tuple[type, ...]] = {
        "string": str,
        "boolean": bool,
        "integer": int,
        "number": (int, float),
        "array": list,
        "object": Mapping,
        "null": type(None),
    }
    target = type_map.get(expected_type)
    return True if target is None else isinstance(value, target)


def _enforce_max_string_length(value: Any, max_chars: int, path: str) -> None:
    """Recursively enforce *max_chars* on every string in a JSON-shaped *value*."""
    if isinstance(value, str):
        if len(value) > max_chars:
            raise ToolExecutionError(
                f"argument {path} exceeds maximum length of {max_chars} characters",
                tool_name="policy_argument_validator",
            )
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _enforce_max_string_length(item, max_chars, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _enforce_max_string_length(item, max_chars, f"{path}[{index}]")


def validate_tool_arguments(
    spec: ToolSpec,
    arguments: Mapping[str, Any],
    *,
    max_argument_count: int = DEFAULT_MAX_ARGUMENT_COUNT,
    max_string_chars: int = DEFAULT_MAX_STRING_ARGUMENT_CHARS,
) -> None:
    """Validate *arguments* against *spec.parameters_schema*, or raise ``ToolExecutionError``."""
    if len(arguments) > max_argument_count:
        raise ToolExecutionError(
            f"Tool {spec.name!r} received {len(arguments)} arguments "
            f"(maximum allowed is {max_argument_count})",
            tool_name=spec.name,
        )

    for key, val in arguments.items():
        _enforce_max_string_length(val, max_string_chars, repr(key))

    schema = spec.parameters_schema
    if not schema:
        return

    required = schema.get("required")
    if isinstance(required, list):
        missing = [req for req in required if isinstance(req, str) and req not in arguments]
        if missing:
            raise ToolExecutionError(
                f"Tool {spec.name!r} missing required argument(s): {missing}",
                tool_name=spec.name,
            )

    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return

    if schema.get("additionalProperties") is False:
        unknown = sorted(key for key in arguments if key not in properties)
        if unknown:
            raise ToolExecutionError(
                f"Tool {spec.name!r} received unexpected argument(s): {unknown}",
                tool_name=spec.name,
            )
    for key, val in arguments.items():
        prop_schema = properties.get(key)
        if not isinstance(prop_schema, Mapping):
            continue
        expected_type = prop_schema.get("type")
        if isinstance(expected_type, str) and not _matches_json_type(val, expected_type):
            raise ToolExecutionError(
                f"Tool {spec.name!r} argument {key!r} must be of type "
                f"{expected_type!r}, got {type(val).__name__}",
                tool_name=spec.name,
            )


def enforce_tool_invocation_policy(
    spec: ToolSpec,
    arguments: Mapping[str, Any],
    *,
    allowed_mutating_tools: Collection[str] = (),
) -> None:
    """Enforce fail-closed ``ToolEffects`` and argument schema before ``Tool.execute``.

    Any tool whose ``spec.effects`` is not :attr:`ToolEffects.READ_ONLY` is
    refused unless its name appears in *allowed_mutating_tools*.
    """
    if spec.effects is not ToolEffects.READ_ONLY and spec.name not in allowed_mutating_tools:
        raise ToolExecutionError(
            f"Tool {spec.name!r} has effect {spec.effects.value!r} and is blocked by the "
            "fail-closed tool policy (only 'read_only' tools may execute without an "
            "explicit mutating-tool grant)",
            tool_name=spec.name,
        )
    validate_tool_arguments(spec, arguments)
