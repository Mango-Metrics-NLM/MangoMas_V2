"""FastAPI application."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from mangomas.api.health import check_ready
from mangomas.api.middleware import AccessLogMiddleware
from mangomas.api.tracing import TraceMiddleware
from mangomas.composition import build_orchestrator
from mangomas.config import get_settings
from mangomas.core import AgentRequest, AgentResponse
from mangomas.errors import (
    AgentNotFound,
    ConfigError,
    LLMBadResponse,
    LLMError,
    LLMTimeout,
    LLMUnavailable,
    MangomasError,
    MaxStepsExceeded,
    PersistenceError,
    SecretsResolutionError,
    ToolExecutionError,
    ToolNotFound,
    UnknownProvider,
)
from mangomas.telemetry import configure_telemetry

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import Orchestrator

logger = logging.getLogger(__name__)

# ── Error → HTTP status mapping ───────────────────────────────────────────────
# Walk the exception's MRO to find the most specific entry.

_ERROR_STATUS: dict[type[MangomasError], int] = {
    UnknownProvider: HTTPStatus.BAD_REQUEST,
    ConfigError: HTTPStatus.BAD_REQUEST,
    ToolNotFound: HTTPStatus.BAD_REQUEST,
    AgentNotFound: HTTPStatus.NOT_FOUND,
    LLMTimeout: HTTPStatus.GATEWAY_TIMEOUT,
    LLMUnavailable: HTTPStatus.SERVICE_UNAVAILABLE,
    LLMBadResponse: HTTPStatus.BAD_GATEWAY,
    LLMError: HTTPStatus.BAD_GATEWAY,
    ToolExecutionError: HTTPStatus.BAD_GATEWAY,
    MaxStepsExceeded: HTTPStatus.UNPROCESSABLE_ENTITY,
    PersistenceError: HTTPStatus.INTERNAL_SERVER_ERROR,
    # A strict secrets backend that is unreachable/denied is an upstream
    # availability problem (parallel to LLMUnavailable); retryable.
    SecretsResolutionError: HTTPStatus.SERVICE_UNAVAILABLE,
    MangomasError: HTTPStatus.INTERNAL_SERVER_ERROR,
}


def _error_status(exc: MangomasError) -> int:
    """Return the most specific HTTP status for *exc* by walking its MRO."""
    for cls in type(exc).__mro__:
        if cls in _ERROR_STATUS:
            return int(_ERROR_STATUS[cls])
    return HTTPStatus.INTERNAL_SERVER_ERROR  # pragma: no cover  -- MangomasError always in MRO


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_telemetry(
        log_level=settings.log_level,
        log_format=settings.log.format,
    )
    app.state.orchestrator = build_orchestrator(settings)
    logger.info("Application started")
    try:
        yield
    finally:
        # Delegate to the orchestrator's single tested teardown path so the
        # FastAPI lifespan, the CLI, and the demo scripts cannot diverge on
        # close ordering or async/sync dispatch rules.
        await app.state.orchestrator.aclose()
        logger.info("Application shut down")


# ── Application factory ───────────────────────────────────────────────────────


def create_app(orchestrator: Orchestrator | None = None) -> FastAPI:
    """Application factory. Pass *orchestrator* to inject a stub for tests."""
    app = FastAPI(
        title="Mango-Mas V2",
        version="0.1.0",
        lifespan=None if orchestrator is not None else _lifespan,
    )

    if orchestrator is not None:
        app.state.orchestrator = orchestrator

    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(TraceMiddleware)

    @app.exception_handler(MangomasError)
    async def _mangomas_error_handler(_request: Request, exc: MangomasError) -> JSONResponse:
        status = _error_status(exc)
        body: dict[str, Any] = {
            "error": exc.code,
            "message": str(exc),
        }
        if exc.detail:
            body["detail"] = exc.detail
        logger.warning("Request error %s: %s", exc.code, exc)
        return JSONResponse(status_code=status, content=body)

    # ── Routes ────────────────────────────────────────────────────────────────

    @app.get("/healthz")
    @app.get("/health")
    async def healthz() -> dict[str, str]:
        """Liveness probe. ``/health`` is retained as a compatibility alias."""
        return {"status": "ok"}

    @app.get("/readyz")
    @app.get("/ready")
    async def readyz() -> JSONResponse:
        """Readiness probe. ``/ready`` is retained as a compatibility alias."""
        orch: Orchestrator = app.state.orchestrator
        report = await check_ready(orch)
        return JSONResponse(status_code=report.http_status, content=report.as_dict())

    @app.get("/agents")
    async def list_agents() -> dict[str, list[str]]:
        orch: Orchestrator = app.state.orchestrator
        return {"agents": orch.list_agents()}

    @app.post("/agents/{name}/invoke", response_model=AgentResponse)
    async def invoke(name: str, request: AgentRequest) -> AgentResponse:
        orch: Orchestrator = app.state.orchestrator
        return await orch.dispatch(name, request)

    @app.post("/agents/{name}/stream")
    async def stream_agent(name: str, request: AgentRequest) -> StreamingResponse:
        orch: Orchestrator = app.state.orchestrator
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

    return app


app = create_app()
