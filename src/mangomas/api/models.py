"""API-layer request/response models.

Api-layer only: the core ``AgentRequest``/``AgentResponse`` models are untouched
and remain the bodies of the ``/agents/*`` routes. These models exist purely for
the ``/workflows/*`` HTTP surface (spec 0008 / ADR-0012).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from mangomas.core import AgentRequest


class WorkflowRunRequest(BaseModel):
    """Body for ``POST /workflows/run``.

    Carries the ``request`` (an :class:`AgentRequest` fed to the graph's root
    node) plus an optional per-request ``definition`` (inline JSON or a path)
    that overrides ``settings.workflow.definition``.
    """

    request: AgentRequest
    definition: str | None = Field(default=None)


class WorkflowValidateRequest(BaseModel):
    """Body for ``POST /workflows/validate``.

    An optional ``definition`` (inline JSON or a path); falls back to
    ``settings.workflow.definition`` when omitted.
    """

    definition: str | None = Field(default=None)


class WorkflowValidateResponse(BaseModel):
    """Result of a successful ``POST /workflows/validate``."""

    ok: bool = True
    name: str
    root_kind: str
