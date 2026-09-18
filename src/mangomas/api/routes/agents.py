"""Agent routes: conversation history, invocation, and SSE streaming.

The router is a factory function (not a module-level ``APIRouter``) because the
``/history`` Query bounds must be read from ``APISettings`` at ``create_app``
call time — tests (and redeploys) clear the settings cache and set env vars
before building an app, and a module-level router would freeze the first
process-wide values instead.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing
from typing import TYPE_CHECKING, Any, cast

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from mangomas.api.auth import require_auth
from mangomas.core import AgentRequest, AgentResponse

logger = logging.getLogger(__name__)

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
        # Metrics are emitted inside Orchestrator.dispatch (ADR-0026) — the
        # route keeps only HTTP concerns; emitting here again would
        # double-count. A MangomasError flows to the shared handler for its
        # HTTP envelope.
        orch: Orchestrator = http_request.app.state.orchestrator
        return await orch.dispatch(name, request)

    @router.post("/agents/{name}/stream", dependencies=[Depends(require_auth)])
    async def stream_agent(
        name: str, request: AgentRequest, http_request: Request
    ) -> StreamingResponse:
        orch: Orchestrator = http_request.app.state.orchestrator
        # stream_dispatch validates the agent eagerly — an AgentNotFound here
        # is raised before any streaming begins, so the exception handler can
        # still return a proper 404 JSON response. It runs *first* so the
        # orchestrator's pre-stream error metrics (ADR-0026) are the ones that
        # record it; the degraded query below then cannot raise, because the
        # agent is known to exist. The route emits no metrics of its own —
        # doing so again would double-count.
        stream_iter: AsyncIterator[str] = await orch.stream_dispatch(name, request)
        degraded = not orch.agent_supports_streaming(name)

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
            # A mid-drain MangomasError propagates untouched: the 200 +
            # text/event-stream headers are already on the wire, so no error
            # envelope can run — the SSE stream simply ends (no `done` frame,
            # spec-0022 R14). The orchestrator's `_stream_agent` records the
            # error metrics before the exception reaches this loop (ADR-0026);
            # abandonment (GeneratorExit) records nothing — symmetry: full
            # drain <=> persisted <=> counted.
            chunks = 0
            async with aclosing(cast("AsyncGenerator[str, None]", stream_iter)):
                async for chunk in stream_iter:
                    chunks += 1
                    payload = {
                        "event": "token",
                        "data": {"content": chunk},
                        "content": chunk,
                    }
                    yield f"data: {json.dumps(payload)}\n\n".encode()
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
