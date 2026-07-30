"""HTTP middleware: backpressure guards, tenancy scoping, and access logging.

Four ``BaseHTTPMiddleware`` classes, assembled by ``create_app``:

* :class:`MaxBodySizeMiddleware` — rejects a request whose declared
  ``Content-Length`` exceeds the configured cap with ``413`` (ADR-0015).
* :class:`ConcurrencyLimitMiddleware` — rejects requests beyond the in-flight
  cap with ``503``, reject-don't-queue (ADR-0015).
* :class:`TenancyMiddleware` — sets the per-request tenant ``ContextVar`` from
  the configured header so storage scopes its SQL (ADR-0017; opt-in).
* :class:`AccessLogMiddleware` — per-request correlation ids + two structured
  access-log records (DEBUG on arrival, INFO after the response).

Ordering
--------
Starlette runs middleware outermost-last-added. ``create_app`` adds the
backpressure pair FIRST so they sit *inner* to the access/trace loggers — a
rejected 413/503 still flows back through :class:`AccessLogMiddleware` and
carries its ``X-Request-ID`` + access-log line. Within the pair, the body-size
guard is added after the concurrency guard so it sits outer: an oversized
request is rejected before it consumes a concurrency slot. CORS (when
configured) is added last, outermost, which is what preflight requires.

Backpressure rejections build their JSON bodies via the app-wide
:func:`~mangomas.api.errors.error_envelope`, so a 413/503 body has exactly the
same ``{"error", "message"}`` shape as handler-produced error responses.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from http import HTTPStatus

from opentelemetry import baggage
from opentelemetry import context as otel_context
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.types import ASGIApp

from mangomas.api.errors import error_envelope
from mangomas.correlation import (
    correlation_id as _correlation_var,
)
from mangomas.correlation import (
    resolve_correlation_id,
    set_correlation_id,
)
from mangomas.tenancy import resolve_tenant, set_tenant
from mangomas.tenancy import tenant_id as _tenant_var

# ``error_envelope`` is deliberately part of this module's surface: the
# middleware rejections and the app-level exception handler must build the
# identical client-visible body (asserted by tests/test_api_envelope.py).
__all__ = [
    "AccessLogMiddleware",
    "ConcurrencyLimitMiddleware",
    "MaxBodySizeMiddleware",
    "TenancyMiddleware",
    "error_envelope",
]

logger = logging.getLogger(__name__)

_REQUEST_ID_HEADER = "X-Request-ID"
_BAGGAGE_KEY = "mangomas.correlation_id"
_FALLBACK_ERROR_STATUS: int = HTTPStatus.INTERNAL_SERVER_ERROR
_INTERNAL_ERROR_BODY = "Internal Server Error"

# Backpressure rejection statuses + JSON envelope codes.
_REQUEST_TOO_LARGE_STATUS: int = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
_REQUEST_TOO_LARGE_CODE = "request_too_large"
# 503 (not 429): reject-don't-queue per ADR-0015 — a momentarily-at-capacity
# server is a retryable *server* condition, not a per-client rate limit.
_AT_CAPACITY_STATUS: int = HTTPStatus.SERVICE_UNAVAILABLE
_AT_CAPACITY_CODE = "server_at_capacity"


def _json_error(status_code: int, error: str, message: str) -> JSONResponse:
    """Build the app's ``{"error", "message"}`` envelope as a JSONResponse.

    Delegates the body shape to the shared :func:`error_envelope` so the
    middleware rejections and the exception handler can never drift apart.
    """
    return JSONResponse(status_code=status_code, content=error_envelope(error, message))


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


class TenancyMiddleware(BaseHTTPMiddleware):
    """Set the per-request tenant from the configured header (ADR-0017).

    Installed only when tenancy is enabled. Reads + sanitises the header and sets
    the ``tenant_id`` ContextVar (falling back to the configured default when
    absent) so the storage repositories scope their SQL to it; resets it on the
    way out so the tenant never leaks across requests.
    """

    def __init__(self, app: ASGIApp, *, header: str, default: str) -> None:
        super().__init__(app)
        self._header = header
        self._default = default

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        tenant = resolve_tenant(request.headers.get(self._header), self._default)
        # set_tenant is the canonical setter; it returns the ContextVar Token so
        # the finally block can restore the pre-request value exactly.
        token = set_tenant(tenant)
        try:
            return await call_next(request)
        finally:
            _tenant_var.reset(token)


def _emit_access_log(
    request: Request,
    response: Response | None,
    status_code: int,
    correlation: str,
    start: float,
) -> None:
    """Echo the correlation id and emit the INFO access-log record.

    Runs for **every** outbound response, including error envelopes produced by
    FastAPI exception handlers — called from the ``finally`` block (not the
    success branch) so handled MangomasError responses, validation 422s, and any
    other framework-generated 4xx/5xx still carry the same ``X-Request-ID`` the
    client can quote when filing a support ticket.
    """
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


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Structured access logging + correlation id propagation.

    Reads the inbound ``X-Request-ID`` header when present (so upstream services
    and clients can propagate their own correlation id) and falls back to a
    freshly generated 8-hex-char token. Inbound values are sanitised via
    :func:`~mangomas.correlation.sanitize_inbound_correlation_id` to clamp the
    length (max :data:`~mangomas.correlation.MAX_CORRELATION_ID_LENGTH`) and
    strip control characters / CR / LF, which prevents log injection from
    hostile clients.

    The resolved value is:

    * stored on a :class:`contextvars.ContextVar` so log records and downstream
      code in the same async context can read it;
    * attached to the current OpenTelemetry baggage as
      ``mangomas.correlation_id`` so distributed traces carry it across service
      boundaries;
    * echoed on the outgoing response as ``X-Request-ID`` — see
      :func:`_emit_access_log`.

    Emits two structured records per request: **DEBUG** ``→ METHOD /path`` on
    arrival, and **INFO** ``METHOD /path STATUS Xms`` after the response, with
    ``request_id``, ``correlation_id``, ``method``, ``path``, ``status_code``
    and ``latency_ms`` as extra fields for JSON log consumers. ``request_id``
    is retained as an attribute distinct from ``correlation_id`` for backwards
    compatibility with existing log consumers; today they always carry the
    same value.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # Sanitise + clamp the inbound header (or mint a fresh id when absent).
        correlation = resolve_correlation_id(request.headers.get(_REQUEST_ID_HEADER))

        # set_correlation_id is the canonical setter; it returns the ContextVar
        # Token so the finally block can restore the pre-request value exactly.
        ctx_token = set_correlation_id(correlation)
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
            _emit_access_log(request, response, status_code, correlation, start)
            otel_context.detach(otel_token)
            _correlation_var.reset(ctx_token)
