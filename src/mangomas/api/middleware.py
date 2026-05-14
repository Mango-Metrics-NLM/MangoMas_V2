"""HTTP access log middleware.

Attaches a short ``request_id`` to every request and emits two structured log
records per request:

* **DEBUG** — ``→ METHOD /path`` immediately on arrival.
* **INFO** — ``METHOD /path STATUS_CODE Xms`` after the response is sent,
  with ``request_id``, ``method``, ``path``, ``status_code`` and
  ``latency_ms`` as extra fields for JSON log consumers.

The middleware is registered by :func:`~mangomas.api.app.create_app` and reads
``body_truncate`` from application settings when body logging is added in a
later phase.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Structured access logging: one DEBUG on arrival, one INFO on completion."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = uuid.uuid4().hex[:8]
        start = time.perf_counter()

        logger.debug(
            "→ %s %s",
            request.method,
            request.url.path,
            extra={"request_id": request_id},
        )

        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            logger.exception(
                "%s %s unhandled exception",
                request.method,
                request.url.path,
                extra={
                    "request_id": request_id,
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
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "latency_ms": latency_ms,
                },
            )
