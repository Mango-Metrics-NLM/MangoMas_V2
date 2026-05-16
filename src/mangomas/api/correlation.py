"""Per-request correlation IDs.

A correlation id is a short, opaque token used to tie all log records,
trace spans, and downstream calls produced by a single HTTP request
together. It is distinct from the W3C trace id (which is service-mesh
wide) — correlation ids are the user/operator-friendly handle that
appears in support tickets and is echoed back on the HTTP response.

Layering
--------
* :data:`correlation_id` — a :class:`contextvars.ContextVar` holding the
  value for the current request. Async-safe (each request task carries
  its own copy).
* :class:`CorrelationFilter` — a logging filter that injects the current
  value into every log record under the ``correlation_id`` attribute,
  so it can be referenced in JSON log output or text formatters.
* :class:`AccessLogMiddleware` (in :mod:`mangomas.api.middleware`) sets
  the ContextVar from the ``X-Request-ID`` header (or generates a new
  short id when absent), pushes it into the OpenTelemetry baggage so
  downstream service calls inherit it, and echoes the final id on the
  outgoing response.

The value is set per request — outside an active request it falls back
to ``"-"`` in log records, which is distinguishable from a real id.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

_NO_CORRELATION = "-"  # placeholder injected into log records when no id is active

correlation_id: ContextVar[str | None] = ContextVar("mangomas_correlation_id", default=None)


def set_correlation_id(value: str) -> None:
    """Set the correlation id for the current async context."""
    correlation_id.set(value)


def get_correlation_id() -> str | None:
    """Return the correlation id for the current async context (or ``None``)."""
    return correlation_id.get()


def generate_correlation_id() -> str:
    """Return a freshly minted short correlation id (8 hex chars)."""
    return uuid.uuid4().hex[:8]


class CorrelationFilter(logging.Filter):
    """Logging filter that injects ``correlation_id`` into every log record.

    Attach this filter to the root handler in :func:`configure_telemetry`
    alongside :class:`~mangomas.telemetry.TraceContextFilter`. When no
    correlation id is active the field defaults to ``"-"`` so that JSON
    log output always carries the key.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id.get() or _NO_CORRELATION
        return True
