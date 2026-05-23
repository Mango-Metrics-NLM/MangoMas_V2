"""Per-request correlation IDs.

A correlation id is a short, opaque token used to tie all log records,
trace spans, and downstream calls produced by a single HTTP request
together. It is distinct from the W3C trace id (which is service-mesh
wide) — correlation ids are the user/operator-friendly handle that
appears in support tickets and is echoed back on the HTTP response.

The module lives at :mod:`mangomas.correlation` (top-level, alongside
:mod:`mangomas.telemetry`) so that :mod:`mangomas.telemetry` can import
the filter at module load without triggering the
``mangomas.api.__init__`` → :func:`create_app` → :mod:`telemetry` import
cycle.

Layering
--------
* :data:`correlation_id` — a :class:`contextvars.ContextVar` holding the
  value for the current request. Async-safe (each request task carries
  its own copy).
* :class:`CorrelationFilter` — a logging filter that injects the current
  value into every log record under the ``correlation_id`` attribute,
  so it can be referenced in JSON log output or text formatters.
* :class:`~mangomas.api.middleware.AccessLogMiddleware` sets the
  ContextVar from the ``X-Request-ID`` header (or generates a new short
  id when absent), pushes it into the OpenTelemetry baggage so
  downstream service calls inherit it, and echoes the final id on the
  outgoing response.

The value is set per request — outside an active request it falls back
to ``"-"`` in log records, which is distinguishable from a real id.
"""

from __future__ import annotations

import logging
import re
import uuid
from contextvars import ContextVar

# ── Configurable bounds ───────────────────────────────────────────────────────
# Inbound ``X-Request-ID`` headers are clamped to this many characters before
# being stored to prevent log-injection or unbounded growth in observability
# pipelines. 64 is generous enough to fit any reasonable trace/UUID and tight
# enough that misbehaving clients can't push large payloads through the log.
MAX_CORRELATION_ID_LENGTH: int = 64

# Characters allowed in a correlation id. Anything outside this set is
# stripped when sanitising an inbound value — this prevents CR/LF injection
# (line-splitting in log records) and keeps the id safe to embed in HTTP
# headers and JSON. The set covers all canonical UUID/hex/url-safe forms
# without enabling free-form text.
_ALLOWED_CHAR_PATTERN: re.Pattern[str] = re.compile(r"[^A-Za-z0-9_\-./:]")

_NO_CORRELATION = "-"  # placeholder injected into log records when no id is active
_CONTEXT_VAR_NAME = "mangomas_correlation_id"
_GENERATED_ID_HEX_LENGTH = 8

correlation_id: ContextVar[str | None] = ContextVar(_CONTEXT_VAR_NAME, default=None)


# ── Public API ───────────────────────────────────────────────────────────────


def set_correlation_id(value: str) -> None:
    """Set the correlation id for the current async context."""
    correlation_id.set(value)


def get_correlation_id() -> str | None:
    """Return the correlation id for the current async context (or ``None``)."""
    return correlation_id.get()


def generate_correlation_id() -> str:
    """Return a freshly minted short correlation id (8 hex chars)."""
    return uuid.uuid4().hex[:_GENERATED_ID_HEX_LENGTH]


def sanitize_inbound_correlation_id(raw: str | None) -> str | None:
    """Return a safe correlation id derived from an inbound header, or ``None``.

    The transformation is, in order:

    1. ``None`` / empty / whitespace-only → ``None`` (caller should generate).
    2. Strip surrounding whitespace.
    3. Remove characters outside the allowed set (alphanumerics + ``_-./:``)
       — this is the log-injection defence: CR/LF, tabs, control chars all
       get dropped.
    4. Truncate to :data:`MAX_CORRELATION_ID_LENGTH` characters.
    5. If the result is empty (e.g. the inbound value was entirely composed
       of disallowed characters), return ``None``.

    Returning ``None`` is the signal that the caller should mint a fresh id
    via :func:`generate_correlation_id`.
    """
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    cleaned = _ALLOWED_CHAR_PATTERN.sub("", stripped)[:MAX_CORRELATION_ID_LENGTH]
    return cleaned or None


def resolve_correlation_id(inbound: str | None) -> str:
    """Return a safe correlation id — sanitised inbound value or a fresh one."""
    return sanitize_inbound_correlation_id(inbound) or generate_correlation_id()


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
