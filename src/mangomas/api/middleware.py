"""HTTP access log middleware with per-request correlation IDs.

Attaches a short correlation id to every request and emits two structured log
records per request:

* **DEBUG** — ``→ METHOD /path`` immediately on arrival.
* **INFO** — ``METHOD /path STATUS_CODE Xms`` after the response is sent,
  with ``request_id``, ``correlation_id``, ``method``, ``path``,
  ``status_code`` and ``latency_ms`` as extra fields for JSON log consumers.

Correlation IDs
---------------
The middleware reads the inbound ``X-Request-ID`` header when present (so
upstream services and clients can propagate their own correlation id) and
falls back to a freshly generated 8-hex-char token. Inbound values are
sanitised via :func:`~mangomas.correlation.sanitize_inbound_correlation_id`
to clamp the length (max :data:`~mangomas.correlation.MAX_CORRELATION_ID_LENGTH`)
and strip control characters / CR / LF, which prevents log injection from
hostile clients.

The resolved value is:

* stored on a :class:`contextvars.ContextVar` so log records and downstream
  code in the same async context can read it;
* attached to the current OpenTelemetry baggage as
  ``mangomas.correlation_id`` so distributed traces carry it across service
  boundaries;
* echoed on the outgoing response as ``X-Request-ID`` so clients can
  reference it in support requests — **including** error responses produced
  by FastAPI exception handlers (the header is set in the ``finally`` block,
  so handled errors carry the same correlation id as successful responses).

``request_id`` is retained as an attribute distinct from ``correlation_id``
for backwards compatibility with existing log consumers; today they always
carry the same value.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from opentelemetry import baggage
from opentelemetry import context as otel_context
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.types import ASGIApp

from mangomas.correlation import (
    correlation_id as _correlation_var,
)
from mangomas.correlation import (
    resolve_correlation_id,
)

logger = logging.getLogger(__name__)

_REQUEST_ID_HEADER = "X-Request-ID"
_BAGGAGE_KEY = "mangomas.correlation_id"
_FALLBACK_ERROR_STATUS = 500
_INTERNAL_ERROR_BODY = "Internal Server Error"

_REQUEST_TOO_LARGE_STATUS = 413
_TOO_MANY_REQUESTS_STATUS = 503


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
            return JSONResponse(
                status_code=_REQUEST_TOO_LARGE_STATUS,
                content={
                    "error": "request_too_large",
                    "message": f"request body exceeds {self._max_bytes} bytes",
                },
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
            return JSONResponse(
                status_code=_TOO_MANY_REQUESTS_STATUS,
                content={
                    "error": "too_many_requests",
                    "message": "server at capacity; retry later",
                },
            )
        self._in_flight += 1
        try:
            return await call_next(request)
        finally:
            self._in_flight -= 1


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Structured access logging + correlation id propagation."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # Sanitise + clamp the inbound header (or mint a fresh id when absent).
        correlation = resolve_correlation_id(request.headers.get(_REQUEST_ID_HEADER))

        ctx_token = _correlation_var.set(correlation)
        baggage_ctx = baggage.set_baggage(_BAGGAGE_KEY, correlation)
        otel_token = otel_context.attach(baggage_ctx)

        start = time.perf_counter()

        logger.debug(
            "→ %s %s",
            request.method,
            request.url.path,
            extra={"request_id": correlation, "correlation_id": correlation},
        )

        status_code = _FALLBACK_ERROR_STATUS
        response: Response | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            # An unhandled exception (one without a registered FastAPI handler)
            # propagated out of the route. Log the full traceback with the
            # correlation id attached, then synthesise a minimal 500 response
            # *here* — instead of re-raising and letting Starlette's default
            # ServerErrorMiddleware build the response — so we can attach the
            # X-Request-ID header. Clients can quote the id even on a crash.
            logger.exception(
                "%s %s unhandled exception",
                request.method,
                request.url.path,
                extra={
                    "request_id": correlation,
                    "correlation_id": correlation,
                    "method": request.method,
                    "path": request.url.path,
                },
            )
            response = PlainTextResponse(
                _INTERNAL_ERROR_BODY,
                status_code=_FALLBACK_ERROR_STATUS,
            )
            status_code = _FALLBACK_ERROR_STATUS
            return response
        finally:
            # Echo the correlation id on **every** outbound response, including
            # error envelopes produced by FastAPI exception handlers. Setting
            # the header here (not in the success branch) ensures handled
            # MangomasError responses, validation 422s, and any other
            # framework-generated 4xx/5xx still carry the same X-Request-ID
            # the client can quote when filing a support ticket.
            if response is not None:
                response.headers[_REQUEST_ID_HEADER] = correlation

            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.info(
                "%s %s %d %.1fms",
                request.method,
                request.url.path,
                status_code,
                latency_ms,
                extra={
                    "request_id": correlation,
                    "correlation_id": correlation,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "latency_ms": latency_ms,
                },
            )
            otel_context.detach(otel_token)
            _correlation_var.reset(ctx_token)
