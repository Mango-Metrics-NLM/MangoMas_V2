"""Optional API authentication (spec 0010 / ADR-0014).

A default-OFF bearer / API-key check. When enabled, the expected token is
resolved once (from ``AuthSettings.secret_ref`` via the ``SecretsProvider`` seam)
and compared in constant time against an inbound ``Authorization: Bearer <token>``
or ``X-API-Key: <token>`` header. Disabled → :func:`require_auth` is a no-op
pass-through, and health/readiness probes are never guarded.
"""

from __future__ import annotations

import logging
import secrets as _secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Request

from mangomas.errors import MangomasError
from mangomas.secrets import secrets_registry

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.config import Settings

_BEARER_PREFIX = "Bearer "

# Credentials are compared as *bytes*, and each side is re-encoded with the
# inverse of the decode that produced its ``str`` — so the comparison is over
# exactly the bytes the client sent versus exactly the bytes the operator
# configured, with no lossy round-trip in between.
#
# ASGI/PEP 3333 decodes header bytes as latin-1, which is byte-transparent, so
# latin-1 is its exact inverse. The expected token is text and encodes as UTF-8.
_HEADER_ENCODING = "latin-1"
_EXPECTED_ENCODING = "utf-8"

# ``surrogateescape`` is the inverse of the decode ``os.environ`` itself uses:
# on POSIX a non-UTF-8 env byte arrives as a lone *low* surrogate, and a plain
# ``.encode("utf-8")`` would raise on it — reintroducing, for the env secrets
# backend, the very 500 this encoding step exists to remove.
_ENCODE_ERRORS = "surrogateescape"


class AuthenticationError(MangomasError):
    """Raised when a request fails API authentication (mapped to HTTP 401)."""

    code = "authentication_error"


@dataclass(frozen=True)
class AuthState:
    """Resolved auth configuration stored on ``app.state.auth``."""

    enabled: bool
    expected_token: str | None


def resolve_auth_state(settings: Settings) -> AuthState:
    """Resolve the expected token once from settings + the ``SecretsProvider``.

    Returns a disabled state (no secret lookup) when auth is off. When on,
    resolves ``secret_ref`` via the configured provider; a provider that is
    unregistered or returns ``None`` yields ``expected_token=None`` so
    :func:`require_auth` fails closed.
    """
    auth_cfg = settings.auth
    if not auth_cfg.enabled or not auth_cfg.secret_ref:
        return AuthState(enabled=auth_cfg.enabled, expected_token=None)
    try:
        provider = secrets_registry.get(settings.secrets.provider)
    except MangomasError:
        # Fail closed, but never silently: this branch rejects every request for
        # the rest of the process's life, and it used to emit nothing at all. A
        # provider-ordering bug that 401'd a correctly configured deployment was
        # invisible for exactly this reason.
        logger.error(
            "Auth enabled but the secrets provider is not registered — every "
            "request will be rejected",
            extra={
                "secrets_provider": settings.secrets.provider,
                "available_providers": secrets_registry.available(),
            },
        )
        return AuthState(enabled=True, expected_token=None)

    expected = provider.get(auth_cfg.secret_ref)
    if expected is None:
        logger.error(
            "Auth enabled but the secret reference resolved to nothing — every "
            "request will be rejected",
            extra={"secrets_provider": settings.secrets.provider},
        )
    return AuthState(enabled=True, expected_token=expected)


def _extract_token(request: Request) -> str | None:
    """Return the presented token from ``Authorization: Bearer`` or ``X-API-Key``."""
    authorization = request.headers.get("Authorization")
    if authorization and authorization.startswith(_BEARER_PREFIX):
        return authorization[len(_BEARER_PREFIX) :].strip() or None
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return api_key.strip() or None
    return None


def _to_comparable_bytes(value: str, encoding: str) -> bytes | None:
    """Encode ``value`` for a constant-time comparison, or ``None`` if it cannot be.

    Returning ``None`` rather than propagating ``UnicodeEncodeError`` keeps the
    caller fail-closed (401) instead of letting an encoding accident escape as a
    500. ``surrogateescape`` covers the lone low surrogates ``os.environ``
    produces; a lone *high* surrogate still cannot be encoded by any handler, and
    that residue is what this guard absorbs.
    """
    try:
        return value.encode(encoding, errors=_ENCODE_ERRORS)
    except UnicodeEncodeError:
        return None


async def require_auth(request: Request) -> None:
    """FastAPI dependency: enforce API auth when enabled (no-op otherwise)."""
    auth: AuthState = request.app.state.auth
    if not auth.enabled:
        return
    if auth.expected_token is None:
        # Fail-closed: auth is on but the expected secret did not resolve.
        raise AuthenticationError(
            "authentication is enabled but no API key is configured",
            detail="unresolved auth secret_ref",
        )
    presented = _extract_token(request)
    if presented is None:
        raise AuthenticationError("invalid or missing API credentials")

    # Compare bytes, never str. ``compare_digest`` accepts a ``str`` only when it
    # is pure ASCII and raises ``TypeError`` otherwise, and ASGI hands us header
    # text decoded from arbitrary wire bytes — so one 0x80+ byte from an
    # *unauthenticated* client used to escape as a 500 through
    # AccessLogMiddleware's catch-all, skipping the JSON error envelope and
    # writing a traceback per request. A non-ASCII configured token did the same
    # on every request.
    #
    # Encoding, rather than ``==`` or an ASCII pre-check, is what preserves the
    # property this comparison exists for: ``compare_digest``'s bytes path is the
    # same fixed-time comparison as its ASCII-str path, so no timing signal about
    # the expected token is reintroduced. Both encodings are injective, so this
    # neither merges two distinct credentials nor splits a matching pair.
    presented_bytes = _to_comparable_bytes(presented, _HEADER_ENCODING)
    expected_bytes = _to_comparable_bytes(auth.expected_token, _EXPECTED_ENCODING)
    if (
        presented_bytes is None
        or expected_bytes is None
        or not _secrets.compare_digest(presented_bytes, expected_bytes)
    ):
        raise AuthenticationError("invalid or missing API credentials")
