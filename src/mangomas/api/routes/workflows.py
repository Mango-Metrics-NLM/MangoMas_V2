"""Workflow routes: declarative graph execution and validation (ADR-0012).

Replaces the former ``app.py::_register_workflow_routes`` closure with a router
factory, keeping the graph-source resolution on the shared
``workflow.resolve_workflow_source`` seam (CLI and HTTP cannot diverge).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request

from mangomas.api.auth import require_auth
from mangomas.api.models import (
    WorkflowRunRequest,
    WorkflowValidateRequest,
    WorkflowValidateResponse,
)
from mangomas.config import get_settings
from mangomas.core import AgentResponse
from mangomas.workflow import execute_workflow, load_workflow, resolve_workflow_source

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import Orchestrator


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
        graph = load_workflow(source)
        return await execute_workflow(graph, body.request, orch=orch)

    @router.post(
        "/workflows/validate",
        response_model=WorkflowValidateResponse,
        dependencies=[Depends(require_auth)],
    )
    async def workflow_validate(body: WorkflowValidateRequest) -> WorkflowValidateResponse:
        """Parse and validate a workflow graph without running it (no LLM I/O)."""
        source = resolve_workflow_source(body.definition, get_settings().workflow)
        graph = load_workflow(source)
        return WorkflowValidateResponse(name=graph.name, root_kind=graph.root.kind)

    return router
