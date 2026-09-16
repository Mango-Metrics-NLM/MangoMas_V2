"""Declarative workflow-graph settings (`MANGOMAS_WORKFLOW__*`)."""

from __future__ import annotations

from pydantic import BaseModel, model_validator

# Declarative multi-agent workflow-graph defaults (spec 0005 / ADR-0011).
# All OFF/None so an environment without ``MANGOMAS_WORKFLOW__*`` behaves
# byte-identically to today. ``definition`` is a path to a JSON graph file OR
# an inline JSON string; ``schema_version`` is the currently supported graph
# schema (the graph declares its own ``schema_version``, validated at load).
DEFAULT_WORKFLOW_ENABLED: bool = False


DEFAULT_WORKFLOW_DEFINITION: str | None = None


DEFAULT_WORKFLOW_SCHEMA_VERSION: int = 1


DEFAULT_WORKFLOW_LOOP_MAX_STEPS: int = 5


# True preserves the documented behaviour: a per-request ``definition`` runs
# even when the feature is disabled. Set False to require server-configured
# graphs only (ADR-0033) — a deployment where one shared credential should not
# authorise composing and running an arbitrary agent graph.
DEFAULT_WORKFLOW_ALLOW_INLINE_DEFINITION: bool = True


class WorkflowSettings(BaseModel):
    """Declarative multi-agent workflow-graph configuration (spec 0005).

    Gated by ``enabled`` (default ``False``) exactly like :class:`MemorySettings`
    / :class:`EmbeddingSettings`, so an environment without ``MANGOMAS_WORKFLOW__*``
    sees no behaviour change. ``definition`` is either a filesystem path to a JSON
    graph or an inline JSON string; it is only consulted when ``enabled`` (or when
    the CLI ``--definition`` flag overrides it). The graph itself declares its
    ``schema_version``, validated by :func:`mangomas.workflow.load_workflow`.
    """

    enabled: bool = DEFAULT_WORKFLOW_ENABLED
    definition: str | None = DEFAULT_WORKFLOW_DEFINITION
    allow_inline_definition: bool = DEFAULT_WORKFLOW_ALLOW_INLINE_DEFINITION

    @model_validator(mode="after")
    def _validate_workflow(self) -> WorkflowSettings:
        """Require a definition when enabled so an enabled run always has a graph."""
        if self.enabled and not self.definition:
            raise ValueError(
                "workflow.enabled requires workflow.definition "
                "(set MANGOMAS_WORKFLOW__DEFINITION to a path or inline JSON)"
            )
        return self
