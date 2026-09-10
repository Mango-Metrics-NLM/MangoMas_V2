"""Backpressure guards: body-size (413) and in-flight concurrency (503).

Installed inner to the access/trace loggers so a rejected request still
flows back through :class:`~mangomas.api.middleware.AccessLogMiddleware`
and carries ``X-Request-ID`` (ADR-0015). Bodies are built through the
``mangomas.api.errors`` module object so a single patch of
``error_envelope`` reaches both 413 and 503 (ADR-0019 amendment).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from http import HTTPStatus

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

import mangomas.api.errors as api_errors

_REQUEST_TOO_LARGE_STATUS: int = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
_REQUEST_TOO_LARGE_CODE = "request_too_large"
# 503 (not 429): reject-don't-queue per ADR-0015 — a momentarily-at-capacity
# server is a retryable *server* condition, not a per-client rate limit.
_AT_CAPACITY_STATUS: int = HTTPStatus.SERVICE_UNAVAILABLE
_AT_CAPACITY_CODE = "server_at_capacity"


def _json_error(status_code: int, error: str, message: str) -> JSONResponse:
    """Build the app's ``{"error", "message"}`` envelope as a JSONResponse."""
    return JSONResponse(status_code=status_code, content=api_errors.error_envelope(error, message))


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """Reject a request whose declared ``Content-Length`` exceeds ``max_bytes``.

    The check is on the ``Content-Length`` header, so an oversized body is
    rejected with ``413`` before Starlette buffers it. Chunked requests without a
    Content-Length are not bounded here (documented in ADR-0015).
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # A malformed / non-numeric Content-Length is treated as "unknown size"
        # and passed through (not a 500) — the ASGI server rejects bad values
        # upstream, and reject-by-guess would be worse than deferring the check.
        content_length = request.headers.get("content-length")
        if (
            content_length is not None
            and content_length.isdigit()
            and int(content_length) > self._max_bytes
        ):
            return _json_error(
                _REQUEST_TOO_LARGE_STATUS,
                _REQUEST_TOO_LARGE_CODE,
                f"request body exceeds {self._max_bytes} bytes",
            )
        return await call_next(request)


class ConcurrencyLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests beyond ``max_concurrent`` in-flight with ``503``.

    Uses a plain in-flight counter — race-free under asyncio's single-threaded
    event loop, since there is no ``await`` between the check and the increment.
    Excess requests are rejected immediately rather than queued, so a slow
    upstream cannot build an unbounded backlog (ADR-0015).
    """

    def __init__(self, app: ASGIApp, *, max_concurrent: int) -> None:
        super().__init__(app)
        self._max = max_concurrent
        self._in_flight = 0

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if self._in_flight >= self._max:
            return _json_error(
                _AT_CAPACITY_STATUS,
                _AT_CAPACITY_CODE,
                "server at capacity; retry later",
            )
        self._in_flight += 1
        try:
            return await call_next(request)
        finally:
            self._in_flight -= 1
