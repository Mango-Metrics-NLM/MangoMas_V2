"""Per-request tenant identity for tenant-scoped storage (spec 0007 / ADR-0017).

A tenant id is a coarse, opaque scope that isolates one tenant's conversation
turns from another's in a shared deployment. It is distinct from the per-request
**correlation id** (:mod:`mangomas.correlation`): correlation is one-request-wide
and operator-facing, whereas a tenant spans many requests. The two deliberately
share the same ``ContextVar`` + sanitiser shape.

Layering
--------
* :data:`tenant_id` — a :class:`contextvars.ContextVar` holding the active tenant
  for the current request. Async-safe (each request task carries its own copy).
* :class:`~mangomas.api.middleware.TenancyMiddleware` sets it from the configured
  header, falling back to the configured default when absent (installed only when
  ``tenancy.enabled``).
* The storage repositories read :func:`get_tenant` **inside** ``save_turn`` /
  ``list_turns`` to scope their SQL — so the ``TurnRepository`` signature is
  unchanged.

When tenancy is disabled (no middleware) :func:`get_tenant` returns
:data:`DEFAULT_TENANT`, so every row is stamped/filtered as ``"default"`` —
byte-identical to the pre-tenancy behaviour.
"""

from __future__ import annotations

import re
from contextvars import ContextVar

# The implicit tenant used when none is supplied or tenancy is disabled. Single
# source of truth — ``config.py`` imports this for ``TenancySettings.default``.
DEFAULT_TENANT = "default"

# Inbound ``X-Tenant-ID`` headers are clamped to this many characters and stripped
# of anything outside the allowed set, mirroring ``correlation.py``'s
# log-injection defence — CR/LF/control chars can never reach a SQL parameter or
# a log line. The set covers UUID/hex/url-safe forms without free-form text.
MAX_TENANT_ID_LENGTH: int = 64
_ALLOWED_CHAR_PATTERN: re.Pattern[str] = re.compile(r"[^A-Za-z0-9_\-./:]")

_CONTEXT_VAR_NAME = "mangomas_tenant_id"

tenant_id: ContextVar[str | None] = ContextVar(_CONTEXT_VAR_NAME, default=None)


def set_tenant(value: str) -> None:
    """Set the tenant for the current async context."""
    tenant_id.set(value)


def get_tenant() -> str:
    """Return the active tenant, or :data:`DEFAULT_TENANT` when none is set.

    Called inside the storage repositories to scope reads/writes, so a disabled
    deployment (no middleware) transparently uses the single ``"default"`` tenant.
    """
    return tenant_id.get() or DEFAULT_TENANT


def sanitize_tenant(raw: str | None) -> str | None:
    """Return a safe tenant id derived from an inbound header, or ``None``.

    ``None`` / empty / whitespace-only → ``None``; characters outside the allowed
    set are stripped (the log/SQL-injection defence); the result is truncated to
    :data:`MAX_TENANT_ID_LENGTH`. Returning ``None`` signals the caller to fall
    back to the configured default.
    """
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    cleaned = _ALLOWED_CHAR_PATTERN.sub("", stripped)[:MAX_TENANT_ID_LENGTH]
    return cleaned or None


def resolve_tenant(inbound: str | None, default: str) -> str:
    """Return a safe tenant — the sanitised inbound value or *default*."""
    return sanitize_tenant(inbound) or default
