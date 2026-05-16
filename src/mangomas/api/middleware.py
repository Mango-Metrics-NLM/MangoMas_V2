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
falls back to a freshly generated 8-hex-char token. The resolved value is:

* stored on a :class:`contextvars.ContextVar` via
  :func:`~mangomas.api.correlation.set_correlation_id` so log records and
  downstream code in the same async context can read it;
* attached to the current OpenTelemetry baggage as ``mangomas.correlation_id``
  so distributed traces carry it across service boundaries;
* echoed on the outgoing response as ``X-Request-ID`` so clients can
  reference it in support requests.

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
from starlette.responses import Response

from mangomas.api.correlation import (
    correlation_id as _correlation_var,
)
from mangomas.api.correlation import (
    generate_correlation_id,
)

logger = logging.getLogger(__name__)

_REQUEST_ID_HEADER = "X-Request-ID"
_BAGGAGE_KEY = "mangomas.correlation_id"


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Structured access logging + correlation id propagation."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        inbound = request.headers.get(_REQUEST_ID_HEADER)
        correlation = inbound.strip() if inbound and inbound.strip() else generate_correlation_id()

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

        status_code = 500
        response: Response | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers[_REQUEST_ID_HEADER] = correlation
            return response
        except Exception:
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
            raise
        finally:
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
