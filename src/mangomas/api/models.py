"""API-layer request/response models.

Api-layer only: the core ``AgentRequest``/``AgentResponse`` models are untouched
and remain the bodies of the ``/agents/*`` routes. These models exist purely for
the ``/workflows/*`` HTTP surface (spec 0008 / ADR-0012) and the read-only
``GET /llm/profiles`` roster (spec-0040 / ADR-0038).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from mangomas.core import AgentRequest


class WorkflowRunRequest(BaseModel):
    """Body for ``POST /workflows/run``.

    Carries the ``request`` (an :class:`AgentRequest` fed to the graph's root
    node) plus an optional per-request ``definition`` (inline JSON only — a
    path is accepted solely from ``settings.workflow.definition``) that
    overrides the server-configured graph.
    """

    request: AgentRequest
    definition: str | None = Field(default=None)


class WorkflowValidateRequest(BaseModel):
    """Body for ``POST /workflows/validate``.

    An optional ``definition`` (inline JSON only; a path is accepted solely
    from the server-configured source); falls back to
    ``settings.workflow.definition`` when omitted.
    """

    definition: str | None = Field(default=None)


class WorkflowValidateResponse(BaseModel):
    """Result of a successful ``POST /workflows/validate``."""

    ok: bool = True
    name: str
    root_kind: str


class LLMProfileSummary(BaseModel):
    """One row of ``GET /llm/profiles``: the three fields a caller may learn.

    Deliberately no ``base_url``, ``api_key`` or ``secret_ref`` (spec-0040 R6);
    a test asserts the serialised body never carries those names.
    """

    name: str
    provider: str
    model: str


class LLMProfilesResponse(BaseModel):
    """Body of ``GET /llm/profiles``. Empty list when no profile is configured."""

    profiles: list[LLMProfileSummary] = Field(default_factory=list)
