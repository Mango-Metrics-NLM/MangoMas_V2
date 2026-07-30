"""Agent routes: conversation history, invocation, and SSE streaming.

The router is a factory function (not a module-level ``APIRouter``) because the
``/history`` Query bounds must be read from ``APISettings`` at ``create_app``
call time — tests (and redeploys) clear the settings cache and set env vars
before building an app, and a module-level router would freeze the first
process-wide values instead.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator, AsyncIterator
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from mangomas.api.auth import require_auth
from mangomas.core import AgentRequest, AgentResponse
from mangomas.errors import MangomasError
from mangomas.metrics import (
    record_agent_duration,
    record_agent_error,
    record_agent_invocation,
)

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.config import APISettings
    from mangomas.core import Orchestrator


def build_agent_router(api_cfg: APISettings) -> APIRouter:
    """Build the agent router (called from inside ``create_app``).

    *api_cfg* supplies the ``/history`` limit bounds; handlers read the
    orchestrator per-request via ``request.app.state.orchestrator`` so an
    injected stub orchestrator keeps working.
    """
    router = APIRouter()

    @router.get("/history", dependencies=[Depends(require_auth)])
    async def history(
        http_request: Request,
        limit: int = Query(
            default=api_cfg.history_default_limit, ge=1, le=api_cfg.history_max_limit
        ),
    ) -> dict[str, list[dict[str, Any]]]:
        """Return recent persisted turns (HTTP twin of ``mangomas history``).

        When no storage is configured (``ctx.repo is None``) this returns an
        empty list rather than erroring, so the route is safe on a
        storage-less deployment.
        """
        orch: Orchestrator = http_request.app.state.orchestrator
        repo = orch.context.repo
        if repo is None:
            return {"turns": []}
        return {"turns": await repo.list_turns(limit=limit)}

    @router.post(
        "/agents/{name}/invoke",
        response_model=AgentResponse,
        dependencies=[Depends(require_auth)],
    )
    async def invoke(name: str, request: AgentRequest, http_request: Request) -> AgentResponse:
        orch: Orchestrator = http_request.app.state.orchestrator
        start = time.perf_counter()
        try:
            response = await orch.dispatch(name, request)
        except MangomasError as exc:
            # Metrics are no-ops unless MANGOMAS_TELEMETRY__METRICS_ENABLED=true;
            # the error still flows to the shared handler for its HTTP envelope.
            record_agent_invocation(name, "error")
            record_agent_error(name, exc.code)
            raise
        record_agent_invocation(name, "ok")
        record_agent_duration(name, time.perf_counter() - start)
        return response

    @router.post("/agents/{name}/stream", dependencies=[Depends(require_auth)])
    async def stream_agent(
        name: str, request: AgentRequest, http_request: Request
    ) -> StreamingResponse:
        orch: Orchestrator = http_request.app.state.orchestrator
        # AgentNotFound is raised here (before streaming begins) so the
        # exception handler can return a proper 404 JSON response.
        stream_iter: AsyncIterator[str] = await orch.stream_dispatch(name, request)

        async def _events() -> AsyncGenerator[bytes, None]:
            async for chunk in stream_iter:
                payload = {
                    "event": "token",
                    "data": {"content": chunk},
                    "content": chunk,
                }
                yield f"data: {json.dumps(payload)}\n\n".encode()
            yield f"data: {json.dumps({'event': 'done'})}\n\n".encode()

        return StreamingResponse(
            _events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
