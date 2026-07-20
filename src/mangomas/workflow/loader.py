"""Load and validate a :class:`WorkflowGraph` from settings, JSON, or a file.

The graph *definition* is env/Settings-driven: ``MANGOMAS_WORKFLOW__DEFINITION``
is either an inline JSON object or a path to a ``.json`` file. Every parse and
validation failure surfaces as :class:`~mangomas.errors.ConfigError` (HTTP 400)
so a malformed graph is a configuration error, never an opaque crash or a raw
pydantic ``ValidationError`` leaking across the boundary. See spec 0005 / ADR-0007.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE
from mangomas.errors import ConfigError
from mangomas.workflow.models import WorkflowGraph

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.config import WorkflowSettings

logger = logging.getLogger(__name__)


def _truncate(detail: str) -> str:
    """Bound untrusted validator/parse text to the shared error-detail limit."""
    return detail[:DEFAULT_ERROR_DETAIL_TRUNCATE]


def parse_graph(data: Any) -> WorkflowGraph:
    """Build and validate a :class:`WorkflowGraph` from a mapping.

    Raises :class:`ConfigError` (never a raw pydantic error) when *data* is not a
    graph object or fails structural validation (duplicate ids, dangling or
    self edges, a cycle, or a missing required field).
    """
    if not isinstance(data, dict):
        raise ConfigError(
            "Workflow definition must be a JSON object",
            detail=f"got {type(data).__name__}",
        )
    try:
        return WorkflowGraph.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(
            "Invalid workflow graph definition",
            detail=_truncate(str(exc)),
        ) from exc


def load_graph_json(text: str) -> WorkflowGraph:
    """Parse *text* as JSON and build a validated graph. Raises :class:`ConfigError`."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            "Workflow definition is not valid JSON",
            detail=_truncate(str(exc)),
        ) from exc
    return parse_graph(data)


def load_graph_file(path: str | Path) -> WorkflowGraph:
    """Read a JSON graph definition from *path*. Raises :class:`ConfigError`."""
    resolved = Path(path)
    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"Workflow definition file could not be read: {resolved}",
            detail=_truncate(str(exc)),
        ) from exc
    return load_graph_json(text)


def graph_from_settings(cfg: WorkflowSettings) -> WorkflowGraph | None:
    """Return the configured graph, or ``None`` when workflows are disabled.

    ``cfg.definition`` is treated as inline JSON when it starts with ``{``, and
    as a file path otherwise — so both
    ``MANGOMAS_WORKFLOW__DEFINITION=./graph.json`` and an inline
    ``MANGOMAS_WORKFLOW__DEFINITION={"nodes": [...]}`` work. Raises
    :class:`ConfigError` when enabled without a definition.
    """
    if not cfg.enabled:
        return None
    definition = (cfg.definition or "").strip()
    if not definition:
        raise ConfigError(
            "MANGOMAS_WORKFLOW__DEFINITION is required when MANGOMAS_WORKFLOW__ENABLED=true"
        )
    if definition.startswith("{"):
        graph = load_graph_json(definition)
    else:
        graph = load_graph_file(definition)
    logger.info(
        "Workflow graph loaded",
        extra={"event": "workflow_loaded", "workflow": graph.name, "nodes": len(graph.nodes)},
    )
    return graph
