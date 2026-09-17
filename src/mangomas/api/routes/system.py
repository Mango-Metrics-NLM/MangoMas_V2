"""System routes: liveness, readiness, and agent discovery.

Health/readiness probes are deliberately never authenticated (ADR-0014) — an
orchestration platform must be able to probe a pod that has lost its secret.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mangomas.api.health import check_ready

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import Orchestrator


def build_system_router() -> APIRouter:
    """Build the system router (called from inside ``create_app``)."""
    router = APIRouter()

    @router.get("/healthz")
    @router.get("/health")
    async def healthz() -> dict[str, str]:
        """Liveness probe. ``/health`` is retained as a compatibility alias."""
        return {"status": "ok"}

    @router.get("/readyz")
    @router.get("/ready")
    async def readyz(request: Request) -> JSONResponse:
        """Readiness probe. ``/ready`` is retained as a compatibility alias."""
        orch: Orchestrator = request.app.state.orchestrator
        report = await check_ready(orch)
        return JSONResponse(status_code=report.http_status, content=report.as_dict())

    @router.get("/agents")
    async def list_agents(request: Request) -> dict[str, list[str]]:
        orch: Orchestrator = request.app.state.orchestrator
        return {"agents": orch.list_agents()}

    return router
