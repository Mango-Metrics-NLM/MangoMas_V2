"""OpenTelemetry trace middleware for FastAPI / Starlette."""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

from opentelemetry import trace
from opentelemetry.propagate import extract
from opentelemetry.trace import StatusCode
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class TraceMiddleware(BaseHTTPMiddleware):
    """Per-request OpenTelemetry span with W3C TraceContext propagation.

    Extracts an incoming ``traceparent`` / ``tracestate`` header and starts a
    child span so the service participates in a distributed trace.  Sets
    standard ``http.*`` span attributes and marks the span as an error for
    HTTP 5xx responses.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        carrier: dict[str, Any] = dict(request.headers)
        ctx = extract(carrier)

        method = request.method
        path = request.url.path

        with trace.get_tracer(__name__).start_as_current_span(
            f"{method} {path}",
            context=ctx,
            kind=trace.SpanKind.SERVER,
        ) as span:
            span.set_attribute("http.method", method)
            span.set_attribute("http.target", path)
            span.set_attribute("http.url", str(request.url))

            try:
                response: Response = await call_next(request)
            except Exception:
                span.set_status(StatusCode.ERROR)
                raise

            span.set_attribute("http.status_code", response.status_code)
            if response.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR.value:
                span.set_status(StatusCode.ERROR)
            else:
                span.set_status(StatusCode.OK)

            return response
