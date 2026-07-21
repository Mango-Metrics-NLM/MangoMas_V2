"""Load a :class:`WorkflowGraph` from a filesystem path or an inline JSON string.

This is the single boundary where third-party input (JSON) becomes a validated
domain model: every parse/validation failure is normalised to
:class:`~mangomas.errors.ConfigError` (HTTP 400) so callers get one error type.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from mangomas.config import DEFAULT_WORKFLOW_SCHEMA_VERSION
from mangomas.errors import ConfigError
from mangomas.workflow.graph import WorkflowGraph

# Graph schema versions this build understands. Unlike the settings' forward-compat
# *warn*, an unknown graph version is rejected: executing an unknown structure
# would silently misbehave.
SUPPORTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({DEFAULT_WORKFLOW_SCHEMA_VERSION})


def _parse_json(raw: str) -> object:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"workflow definition is not valid JSON: {exc}") from exc


def load_workflow(source: str) -> WorkflowGraph:
    """Parse *source* into a validated :class:`WorkflowGraph`.

    *source* is treated as inline JSON when its stripped form starts with ``{``,
    otherwise as a path to a JSON file. Raises
    :class:`~mangomas.errors.ConfigError` on an unreadable file, invalid JSON, a
    schema-validation failure, or an unsupported ``schema_version``.
    """
    stripped = source.strip()
    if stripped.startswith("{"):
        data = _parse_json(stripped)
    else:
        try:
            text = Path(source).read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"cannot read workflow definition {source!r}: {exc}") from exc
        data = _parse_json(text)

    try:
        graph = WorkflowGraph.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid workflow graph: {exc}") from exc

    if graph.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ConfigError(
            f"unsupported workflow schema_version {graph.schema_version}; "
            f"supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
        )
    return graph
