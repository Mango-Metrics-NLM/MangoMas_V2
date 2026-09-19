"""Load a :class:`WorkflowGraph` from a filesystem path or an inline JSON string.

This is the single boundary where third-party input (JSON) becomes a validated
domain model: every parse/validation failure is normalised to
:class:`~mangomas.errors.ConfigError` (HTTP 400) so callers get one error type.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from mangomas.config import DEFAULT_WORKFLOW_SCHEMA_VERSION, WorkflowSettings
from mangomas.errors import ConfigError
from mangomas.workflow.graph import WorkflowGraph
from mangomas.workflow.validation import validate_structured_acceptance

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.workflow.validation import StructuredAgentFields

# Graph schema versions this build understands. Unlike the settings' forward-compat
# *warn*, an unknown graph version is rejected: executing an unknown structure
# would silently misbehave.
SUPPORTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({DEFAULT_WORKFLOW_SCHEMA_VERSION})


def resolve_workflow_source(definition: str | None, cfg: WorkflowSettings) -> str:
    """Return the effective graph source, or raise ``ConfigError``.

    Single source of the opt-in precedence rule shared by the CLI and the HTTP
    surface. While ``cfg.allow_inline_definition`` is ``True`` (the default,
    preserving today's documented behaviour) an explicit *definition* runs even
    when the feature is disabled — a per-invocation opt-in; otherwise
    ``cfg.enabled`` **and** a configured ``cfg.definition`` are required.

    That per-invocation opt-in is surprising enough to deserve its own switch
    (ADR-0033): ``allow_inline_definition=False`` refuses a caller-supplied
    graph outright — it is rejected, not silently ignored in favour of
    ``cfg.definition`` — so a deployment can require server-configured graphs
    only.

    Surface-neutral — callers map the raised
    :class:`~mangomas.errors.ConfigError` onto their own error surface (HTTP 400 /
    CLI exit 2).
    """
    if definition is not None and not cfg.allow_inline_definition:
        raise ConfigError(
            "inline workflow definitions are disabled "
            "(MANGOMAS_WORKFLOW__ALLOW_INLINE_DEFINITION=false); "
            "use the server-configured MANGOMAS_WORKFLOW__DEFINITION instead"
        )
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


def load_workflow(
    source: str,
    *,
    structured_agents: StructuredAgentFields | None = None,
) -> WorkflowGraph:
    """Parse *source* into a validated :class:`WorkflowGraph`.

    *source* is treated as inline JSON when its stripped form starts with ``{``,
    otherwise as a path to a JSON file. Raises
    :class:`~mangomas.errors.ConfigError` on an unreadable file, invalid JSON, a
    schema-validation failure, or an unsupported ``schema_version``.

    *structured_agents* maps an agent name to its schema's top-level field names
    (``mangomas.composition.agents.STRUCTURED_AGENT_FIELDS``). When supplied, a
    ``loop`` over one of those agents may not accept on a text predicate, and a
    ``json_field`` path must address a field the schema actually has — see
    :mod:`mangomas.workflow.validation`. Keyword-only with a ``None`` default, so
    every pre-existing caller and every previously valid graph behaves
    identically; the checks are opt-in by construction, and the three in-repo call
    sites opt in.
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
    # Last: the semantic checks, which need a fully validated graph to walk.
    validate_structured_acceptance(graph, structured_agents)
    return graph
