"""Structured access logging + correlation id propagation.

Logger name is pinned to ``mangomas.api.middleware`` so ``caplog`` filters on
that name survive this package split (same reason telemetry pins
``mangomas.telemetry``).
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
from starlette.responses import PlainTextResponse, Response

from mangomas.correlation import (
    correlation_id as _correlation_var,
)
from mangomas.correlation import (
    resolve_correlation_id,
    set_correlation_id,
)

logger = logging.getLogger("mangomas.api.middleware")

_REQUEST_ID_HEADER = "X-Request-ID"
_BAGGAGE_KEY = "mangomas.correlation_id"
_FALLBACK_ERROR_STATUS: int = HTTPStatus.INTERNAL_SERVER_ERROR
_INTERNAL_ERROR_BODY = "Internal Server Error"


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
