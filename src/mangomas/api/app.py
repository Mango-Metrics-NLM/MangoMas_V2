"""FastAPI application factory.

Slim assembly point (spec 0014 / M11): middleware ordering, opt-in CORS /
tenancy / auth state, the lifespan, and router inclusion. The pieces live in
sibling modules — ``api/errors.py`` (status mapping + envelope + handler),
``api/models.py`` (workflow bodies), ``api/routes/`` (system / agents /
workflows router factories) — and their previously importable names are
re-exported here permanently (ADR-0019).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mangomas.api.auth import resolve_auth_state
from mangomas.api.errors import (
    _ERROR_STATUS,
    _error_status,
    error_envelope,
    register_error_handler,
)
from mangomas.api.middleware import (
    AccessLogMiddleware,
    ConcurrencyLimitMiddleware,
    MaxBodySizeMiddleware,
    TenancyMiddleware,
)
from mangomas.api.models import (
    WorkflowRunRequest,
    WorkflowValidateRequest,
    WorkflowValidateResponse,
)
from mangomas.api.routes import (
    build_agent_router,
    build_system_router,
    build_workflow_router,
)
from mangomas.api.tracing import TraceMiddleware
from mangomas.composition import build_orchestrator, ensure_secrets_provider
from mangomas.config import get_settings
from mangomas.telemetry import configure_metrics, configure_telemetry

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.config import APISettings
    from mangomas.core import Orchestrator

# Permanent facade (ADR-0019): names that were importable from this module
# before the M11 split stay importable — tests and downstream code pin them.
__all__ = [
    "_ERROR_STATUS",
    "WorkflowRunRequest",
    "WorkflowValidateRequest",
    "WorkflowValidateResponse",
    "_error_status",
    "app",
    "create_app",
    "error_envelope",
]

logger = logging.getLogger(__name__)

# The installed distribution whose version stamps the OpenAPI ``info.version``.
# Named once here — never restate the string at a call site.
_DIST_NAME = "mangomas"

# Fallback ``info.version`` when the distribution is not installed (e.g. a raw
# source checkout without ``pip install -e``). PEP 440 local-version syntax
# makes the "unknown" provenance explicit rather than masquerading as a release.
DEFAULT_APP_VERSION = "0.0.0+unknown"


def _package_version() -> str:
    """Resolve the app version from installed package metadata.

    ``pyproject.toml`` is the single source of truth for the project version;
    deriving it here keeps the OpenAPI document from drifting behind releases
    (it sat at a hardcoded ``0.1.0`` for two minor versions).
    """
    try:
        return _distribution_version(_DIST_NAME)
    except PackageNotFoundError:
        return DEFAULT_APP_VERSION


def _install_backpressure(app: FastAPI, api_cfg: APISettings) -> None:
    """Install the opt-in backpressure guards (no-op when both limits are 0).

    Installed *inner* to the access/trace loggers (see ``create_app``) so a
    rejected 413/503 still flows back through ``AccessLogMiddleware`` and carries
    its ``X-Request-ID`` + access-log line (ADR-0015). The body-size guard is
    added after the concurrency guard so it sits outer — an oversized request is
    rejected before it consumes a concurrency slot.
    """
    if api_cfg.max_concurrent_requests > 0:
        app.add_middleware(
            ConcurrencyLimitMiddleware, max_concurrent=api_cfg.max_concurrent_requests
        )
    if api_cfg.max_body_bytes > 0:
        app.add_middleware(MaxBodySizeMiddleware, max_bytes=api_cfg.max_body_bytes)


def _install_tenancy(app: FastAPI) -> None:
    """Install the opt-in `TenancyMiddleware` (no-op when tenancy is disabled).

    Sets the per-request tenant `ContextVar` the storage repos read; default-OFF
    ⇒ not installed ⇒ every row uses the implicit ``"default"`` tenant (ADR-0017).
    """
    cfg = get_settings().tenancy
    if cfg.enabled:
        app.add_middleware(TenancyMiddleware, header=cfg.header, default=cfg.default)


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_telemetry(
        log_level=settings.log_level,
        log_format=settings.log.format,
        exporter=settings.telemetry.exporter,
    )
    configure_metrics(
        exporter=settings.telemetry.exporter,
        enabled=settings.telemetry.metrics_enabled,
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
        version=_package_version(),
        lifespan=None if orchestrator is not None else _lifespan,
    )

    if orchestrator is not None:
        app.state.orchestrator = orchestrator

    # Backpressure guards are installed FIRST so they end up *inner* to the
    # log/trace middlewares added next — a rejected 413/503 still flows back
    # through AccessLog (X-Request-ID + access-log line). CORS is added last so
    # it sits outermost (correct for preflight).
    api_cfg = get_settings().api
    _install_backpressure(app, api_cfg)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(TraceMiddleware)

    # Opt-in CORS: installed only when an allow-list is configured, so the
    # default (empty) keeps the response headers byte-identical to before. All
    # knobs are env-driven (no hard-coded policy); credentials default off.
    if api_cfg.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=api_cfg.cors_allow_origins,
            allow_credentials=api_cfg.cors_allow_credentials,
            allow_methods=api_cfg.cors_allow_methods,
            allow_headers=api_cfg.cors_allow_headers,
        )

    _install_tenancy(app)

    # Resolve the expected API token once (default-OFF → a no-op pass-through).
    #
    # The provider must be registered *first*. This runs during app
    # construction, strictly before the lifespan calls ``build_orchestrator``,
    # so relying on that call to register a cloud backend left this lookup
    # failing closed to ``expected_token=None`` — a 401 on every request of a
    # correctly configured GCP + auth deployment.
    _settings = get_settings()
    ensure_secrets_provider(_settings.secrets)
    app.state.auth = resolve_auth_state(_settings)

    # This module's logger keeps the historical "mangomas.api.app" record name.
    register_error_handler(app, handler_logger=logger)

    # Routers are built here (not at import time) so per-app config such as the
    # /history Query bounds reads the settings active at create_app call time.
    app.include_router(build_system_router())
    app.include_router(build_agent_router(api_cfg))
    app.include_router(build_workflow_router())

    return app


app = create_app()
