"""Workflow routes: declarative graph execution and validation (ADR-0012).

Replaces the former ``app.py::_register_workflow_routes`` closure with a router
factory, keeping the graph-source resolution on the shared
``workflow.resolve_workflow_source`` seam (CLI and HTTP cannot diverge).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, Depends, Request

from mangomas.api.auth import require_auth
from mangomas.api.models import (
    WorkflowRunRequest,
    WorkflowValidateRequest,
    WorkflowValidateResponse,
)
from mangomas.composition.agents import (
    STRUCTURED_AGENT_FIELDS,
    STRUCTURED_AGENT_FIELDS_EXTRAS_KEY,
)
from mangomas.config import get_settings
from mangomas.core import AgentResponse
from mangomas.workflow import execute_workflow, load_workflow, resolve_workflow_source
from mangomas.workflow.validation import StructuredAgentFields

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import Orchestrator


def _structured_agents(orch: Orchestrator) -> StructuredAgentFields:
    """Return the schema map to validate against, preferring the live one.

    ``build_orchestrator`` publishes a **plugin-inclusive** map on
    ``AgentContext.extras`` — derived from the agents it actually built, so an
    entry-point ``StructuredOutputAgent`` is covered too. The import-time
    ``STRUCTURED_AGENT_FIELDS`` constant is the fallback for an orchestrator
    assembled by hand (tests, library callers), which carries no such extras.
    """
    published = orch.context.extras.get(STRUCTURED_AGENT_FIELDS_EXTRAS_KEY)
    if isinstance(published, Mapping):
        return cast("StructuredAgentFields", published)
    return STRUCTURED_AGENT_FIELDS


def build_workflow_router() -> APIRouter:
    """Build the workflow router (called from inside ``create_app``)."""
    router = APIRouter()

    @router.post(
        "/workflows/run",
        response_model=AgentResponse,
        dependencies=[Depends(require_auth)],
    )
    async def workflow_run(body: WorkflowRunRequest, http_request: Request) -> AgentResponse:
        """Execute a declarative workflow graph and return the final response.

        A per-request ``definition`` runs even when the feature is disabled;
        otherwise ``workflow.enabled`` + a configured definition is required
        (disabled/unset → 400 ``ConfigError``). Graph parse/validation errors
        (400), an unknown agent (404), and ``MaxStepsExceeded`` (422) all flow
        through the shared error handler.
        """
        orch: Orchestrator = http_request.app.state.orchestrator
        source = resolve_workflow_source(body.definition, get_settings().workflow)
        graph = load_workflow(source, structured_agents=_structured_agents(orch))
        return await execute_workflow(graph, body.request, orch=orch)

    @router.post(
        "/workflows/validate",
        response_model=WorkflowValidateResponse,
        dependencies=[Depends(require_auth)],
    )
    async def workflow_validate(
        body: WorkflowValidateRequest, http_request: Request
    ) -> WorkflowValidateResponse:
        """Parse and validate a workflow graph without running it (no LLM I/O).

        Takes the request only to reach the orchestrator's published
        structured-agent map, so ``/workflows/validate`` accepts exactly what
        ``/workflows/run`` would accept. Validating against a different map than
        the runner uses would make this endpoint a liar.
        """
        orch: Orchestrator = http_request.app.state.orchestrator
        source = resolve_workflow_source(body.definition, get_settings().workflow)
        graph = load_workflow(source, structured_agents=_structured_agents(orch))
        return WorkflowValidateResponse(name=graph.name, root_kind=graph.root.kind)

    return router
