"""FastAPI application."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from mangomas.api.health import check_ready
from mangomas.api.middleware import AccessLogMiddleware
from mangomas.api.tracing import TraceMiddleware
from mangomas.composition import build_orchestrator
from mangomas.config import WorkflowSettings, get_settings
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
from mangomas.workflow import execute_workflow, load_workflow

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
    SecretsResolutionError: HTTPStatus.SERVICE_UNAVAILABLE,
    PersistenceError: HTTPStatus.INTERNAL_SERVER_ERROR,
    MangomasError: HTTPStatus.INTERNAL_SERVER_ERROR,
}


def _error_status(exc: MangomasError) -> int:
    """Return the most specific HTTP status for *exc* by walking its MRO."""
    for cls in type(exc).__mro__:
        if cls in _ERROR_STATUS:
            return int(_ERROR_STATUS[cls])
    return HTTPStatus.INTERNAL_SERVER_ERROR  # pragma: no cover  -- MangomasError always in MRO


# ── Workflow request/response models ──────────────────────────────────────────
# Api-layer only: the core AgentRequest/AgentResponse models are untouched.


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


def _resolve_workflow_source(definition: str | None, cfg: WorkflowSettings) -> str:
    """Return the effective graph source, or raise ``ConfigError`` (HTTP 400).

    Mirrors :func:`mangomas.cli.main._resolve_workflow_source`: an explicit
    *definition* runs even when the feature is disabled (per-invocation opt-in);
    otherwise the feature must be enabled AND a definition configured. Both
    failure paths raise :class:`~mangomas.errors.ConfigError`, mapped to 400 by
    :data:`_ERROR_STATUS`.
    """
    if definition is None and not cfg.enabled:
        raise ConfigError(
            "workflow disabled; set MANGOMAS_WORKFLOW__ENABLED=true and "
            "MANGOMAS_WORKFLOW__DEFINITION, or pass 'definition'"
        )
    source = definition or cfg.definition
    if not source:
        raise ConfigError(
            "no workflow definition; set MANGOMAS_WORKFLOW__DEFINITION or pass 'definition'"
        )
    return source


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_telemetry(
        log_level=settings.log_level,
        log_format=settings.log.format,
        exporter=settings.telemetry.exporter,
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

    # Opt-in CORS: installed only when an allow-list is configured, so the
    # default (empty) keeps the response headers byte-identical to before.
    cors_origins = get_settings().api.cors_allow_origins
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

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

    @app.get("/history")
    async def history(limit: int = 10) -> dict[str, list[dict[str, Any]]]:
        """Return recent persisted turns (HTTP twin of ``mangomas history``).

        When no storage is configured (``ctx.repo is None``) this returns an
        empty list rather than erroring, so the route is safe on a
        storage-less deployment.
        """
        orch: Orchestrator = app.state.orchestrator
        repo = orch.context.repo
        if repo is None:
            return {"turns": []}
        return {"turns": await repo.list_turns(limit=limit)}

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

    @app.post("/workflows/run", response_model=AgentResponse)
    async def workflow_run(body: WorkflowRunRequest) -> AgentResponse:
        """Execute a declarative workflow graph and return the final response.

        A per-request ``definition`` runs even when the feature is disabled;
        otherwise ``workflow.enabled`` + a configured definition is required
        (disabled/unset → 400 ``ConfigError``). Graph parse/validation errors
        (400), an unknown agent (404), and ``MaxStepsExceeded`` (422) all flow
        through the shared error handler.
        """
        orch: Orchestrator = app.state.orchestrator
        source = _resolve_workflow_source(body.definition, get_settings().workflow)
        graph = load_workflow(source)
        return await execute_workflow(graph, body.request, orch=orch)

    @app.post("/workflows/validate", response_model=WorkflowValidateResponse)
    async def workflow_validate(body: WorkflowValidateRequest) -> WorkflowValidateResponse:
        """Parse and validate a workflow graph without running it (no LLM I/O)."""
        source = _resolve_workflow_source(body.definition, get_settings().workflow)
        graph = load_workflow(source)
        return WorkflowValidateResponse(name=graph.name, root_kind=graph.root.kind)

    return app


app = create_app()
