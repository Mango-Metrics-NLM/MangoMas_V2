"""Load a :class:`WorkflowGraph` from a filesystem path or an inline JSON string.

This is the single boundary where third-party input (JSON) becomes a validated
domain model: every parse/validation failure is normalised to
:class:`~mangomas.errors.ConfigError` (HTTP 400) so callers get one error type.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from mangomas.config import DEFAULT_WORKFLOW_SCHEMA_VERSION, WorkflowSettings
from mangomas.errors import ConfigError
from mangomas.workflow.graph import WorkflowGraph

# Graph schema versions this build understands. Unlike the settings' forward-compat
# *warn*, an unknown graph version is rejected: executing an unknown structure
# would silently misbehave.
SUPPORTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({DEFAULT_WORKFLOW_SCHEMA_VERSION})


def resolve_workflow_source(definition: str | None, cfg: WorkflowSettings) -> str:
    """Return the effective graph source, or raise ``ConfigError``.

    Single source of the opt-in precedence rule shared by the CLI and the HTTP
    surface: an explicit *definition* runs even when the feature is disabled
    (per-invocation opt-in); otherwise ``cfg.enabled`` **and** a configured
    ``cfg.definition`` are required. Surface-neutral — callers map the raised
    :class:`~mangomas.errors.ConfigError` onto their own error surface (HTTP 400 /
    CLI exit 2).
    """
    if definition is None and not cfg.enabled:
        raise ConfigError(
            "workflow disabled; enable it (MANGOMAS_WORKFLOW__ENABLED=true + "
            "MANGOMAS_WORKFLOW__DEFINITION) or pass a definition"
        )
    source = definition or cfg.definition
    if not source:
        raise ConfigError(
            "no workflow definition; set MANGOMAS_WORKFLOW__DEFINITION or pass a definition"
        )
    return source


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
            # Use the stripped source (not the raw one) so a path arriving with a
            # trailing newline — common from env vars / CLI args — does not fail
            # spuriously; the inline-vs-path branch above already used ``stripped``.
            text = Path(stripped).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            # UnicodeDecodeError subclasses ValueError (not OSError), so a binary
            # or non-UTF-8 file must be caught explicitly to reach the ConfigError
            # normalization boundary rather than crashing.
            raise ConfigError(f"cannot read workflow definition {stripped!r}: {exc}") from exc
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
