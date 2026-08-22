"""HTTP error surface: status mapping, the shared envelope, and the handler.

Single home for the API layer's error contract (spec 0014 / M11):

* :data:`_ERROR_STATUS` — the ``MangomasError`` subclass → HTTP status table.
  Every ``MangomasError`` must be mapped here (directly or via a base class).
* :func:`_error_status` — resolves the most specific status by MRO walk.
* :func:`error_envelope` — the one place the ``{"error", "message"[, "detail"]}``
  JSON body shape is constructed. Both the exception handler below and the
  middleware backpressure rejections (``middleware._json_error``) build their
  bodies through it, so the envelope cannot drift between the two paths.
* :func:`register_error_handler` — installs the ``MangomasError`` handler on an
  app; called from ``create_app``.
"""

from __future__ import annotations

import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from mangomas.api.auth import AuthenticationError
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
    StepTimeout,
    ToolExecutionError,
    ToolNotFound,
    UnknownProvider,
)

logger = logging.getLogger(__name__)

# ── Error → HTTP status mapping ───────────────────────────────────────────────
# Walk the exception's MRO to find the most specific entry.

_ERROR_STATUS: dict[type[MangomasError], int] = {
    UnknownProvider: HTTPStatus.BAD_REQUEST,
    ConfigError: HTTPStatus.BAD_REQUEST,
    ToolNotFound: HTTPStatus.BAD_REQUEST,
    AuthenticationError: HTTPStatus.UNAUTHORIZED,
    AgentNotFound: HTTPStatus.NOT_FOUND,
    LLMTimeout: HTTPStatus.GATEWAY_TIMEOUT,
    StepTimeout: HTTPStatus.GATEWAY_TIMEOUT,
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


def error_envelope(code: str, message: str, detail: str = "") -> dict[str, str]:
    """Build the app-wide ``{"error", "message"[, "detail"]}`` JSON error body.

    The single construction point for the error envelope: the ``MangomasError``
    exception handler and the middleware backpressure rejections both call this,
    so a shape change happens exactly once, repo-wide. ``detail`` is included
    only when non-empty — a detail-less error keeps the two-key shape.
    """
    body = {"error": code, "message": message}
    if detail:
        body["detail"] = detail
    return body


def register_error_handler(app: FastAPI, *, handler_logger: logging.Logger | None = None) -> None:
    """Install the ``MangomasError`` → JSON-envelope exception handler on *app*.

    *handler_logger* defaults to this module's logger; ``create_app`` passes the
    ``mangomas.api.app`` logger so the ``"Request error <code>"`` warning keeps
    its historical logger name — existing log consumers (and tests) filter on it.
    """
    log = handler_logger if handler_logger is not None else logger

    @app.exception_handler(MangomasError)
    async def _mangomas_error_handler(_request: Request, exc: MangomasError) -> JSONResponse:
        status = _error_status(exc)
        body = error_envelope(exc.code, str(exc), exc.detail)
        log.warning("Request error %s: %s", exc.code, exc)
        return JSONResponse(status_code=status, content=body)
