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
from contextlib import aclosing
from typing import TYPE_CHECKING, Any, cast

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
        start = time.perf_counter()
        try:
            # Computed before streaming begins: it raises AgentNotFound in the
            # same pre-stream window as stream_dispatch's own check, so the
            # exception handler can still return a proper 404 JSON response.
            degraded = not orch.agent_supports_streaming(name)
            stream_iter: AsyncIterator[str] = await orch.stream_dispatch(name, request)
        except MangomasError as exc:
            # Pre-stream failure: mirror invoke's metrics-then-envelope path.
            record_agent_invocation(name, "error")
            record_agent_error(name, exc.code)
            raise

        async def _events() -> AsyncGenerator[bytes, None]:
            # ``aclosing`` guarantees ``stream_iter.aclose()`` runs promptly
            # (synchronously, as part of unwinding this generator) when the
            # client disconnects mid-stream. Without it, a bare ``async for``
            # does not close the iterable it's driving on early exit — Python
            # would eventually run cleanup via the event loop's async-generator
            # GC finalizer, but not deterministically, which would leave the
            # harness's `harness.agent_invoke` span (see composition.py's
            # `_traced_stream`) open until that eventual GC pass instead of
            # ending it the moment the client actually disconnects.
            #
            # ``stream_dispatch`` is typed as the more general
            # ``AsyncIterator[str]`` (the ``StreamingAgent.stream`` protocol
            # doesn't guarantee ``aclose()``), but every concrete
            # implementation on this path — ``Orchestrator._stream_agent`` and
            # the harness's ``_traced_stream`` alike — is an async generator,
            # which always has one.
            chunks = 0
            try:
                async with aclosing(cast("AsyncGenerator[str, None]", stream_iter)):
                    async for chunk in stream_iter:
                        chunks += 1
                        payload = {
                            "event": "token",
                            "data": {"content": chunk},
                            "content": chunk,
                        }
                        yield f"data: {json.dumps(payload)}\n\n".encode()
            except MangomasError as exc:
                # Mid-drain failure: the 200 + text/event-stream headers are
                # already on the wire, so no error envelope can run — the SSE
                # stream simply ends (no `done` frame, spec-0022 R14). Record
                # the error metrics, then re-raise so the truncation contract
                # is unchanged.
                record_agent_invocation(name, "error")
                record_agent_error(name, exc.code)
                raise
            # Full drain (client abandonment raises GeneratorExit above and
            # records nothing — symmetry: full drain <=> persisted <=> counted).
            record_agent_invocation(name, "ok")
            record_agent_duration(name, time.perf_counter() - start)
            metadata = {
                "event": "metadata",
                "data": {"agent": name, "degraded": degraded, "chunks": chunks},
            }
            yield f"data: {json.dumps(metadata)}\n\n".encode()
            yield f"data: {json.dumps({'event': 'done'})}\n\n".encode()

        return StreamingResponse(
            _events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
