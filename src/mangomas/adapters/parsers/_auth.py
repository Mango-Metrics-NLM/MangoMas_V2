"""Identity-token seam for a parser behind a private Cloud Run service (spec-0035 R3).

``auth_mode="google_id_token"`` sends ``Authorization: Bearer <identity token>``
— the primary control for a docling-serve deployment with no unauthenticated
invoke (ADR-0036 §3). The token comes from an :class:`IdTokenProvider` so tests
inject a fake and never touch Google; :class:`GoogleIdTokenProvider` is the
default, minting through Application Default Credentials via ``google-auth``,
which is lazy-imported so this module imports without the ``gcp``/``vertex``
extra.

Caching is the caller's job (the parser re-mints a configured margin before
expiry), so a provider only reports ``(token, expiry_epoch_seconds)``.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable
from typing import Final, Protocol, runtime_checkable

from mangomas.errors import ConfigError, MangomasError

_JWT_SEGMENTS: Final[int] = 3
_JWT_PAYLOAD_INDEX: Final[int] = 1
_EXP_CLAIM: Final[str] = "exp"
_B64_BLOCK: Final[int] = 4

GOOGLE_AUTH_INSTALL_HINT: Final[str] = (
    "auth_mode='google_id_token' requires google-auth; install it with "
    "pip install 'mangomas[gcp]' (or 'mangomas[vertex]')"
)


@runtime_checkable
class IdTokenProvider(Protocol):
    """Mint an identity token for an audience."""

    async def fetch(self, audience: str) -> tuple[str, float]:
        """Return ``(token, expiry)`` with ``expiry`` in epoch seconds."""
        ...


def id_token_expiry(token: str) -> float:
    """Return the ``exp`` claim of a JWT, decoding its payload **without** verifying.

    Verification is deliberately skipped: the token is one we just minted
    ourselves through ADC, and the only thing read from it is when to mint the
    next one. It is never trusted as an assertion about anyone. Errors name the
    failure only — never the token, which is a bearer credential.
    """
    parts = token.split(".")
    if len(parts) != _JWT_SEGMENTS:
        raise ConfigError("identity token is not a JWT", detail=f"segments={len(parts)}")
    segment = parts[_JWT_PAYLOAD_INDEX]
    padded = segment + "=" * (-len(segment) % _B64_BLOCK)
    try:
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except ValueError as exc:  # binascii.Error, UnicodeDecodeError, JSONDecodeError
        raise ConfigError(
            "identity token payload is not decodable", detail=type(exc).__name__
        ) from None
    expiry = claims.get(_EXP_CLAIM) if isinstance(claims, dict) else None
    if isinstance(expiry, bool) or not isinstance(expiry, int | float):
        raise ConfigError("identity token carries no numeric exp claim")
    return float(expiry)


def _fetch_google_id_token(audience: str) -> str:
    """Mint an ADC identity token for *audience* (blocking; run in a thread).

    The ``google-auth`` import is lazy so this module imports without the extra;
    a missing SDK surfaces here, at first use, as a ``ConfigError`` naming the
    install command. Only the mint call itself needs the extra to run.
    """
    try:
        from google.auth.transport import requests as google_requests  # noqa: PLC0415

        # Reached only once the line above imported, i.e. with the extra installed.
        from google.oauth2 import (  # noqa: PLC0415  # pragma: no cover - requires the extra
            id_token as google_id_token,
        )
    except ImportError as exc:
        raise ConfigError(GOOGLE_AUTH_INSTALL_HINT) from exc
    return str(  # pragma: no cover - requires the gcp/vertex extra
        google_id_token.fetch_id_token(google_requests.Request(), audience)
    )


class GoogleIdTokenProvider:
    """Default :class:`IdTokenProvider`: ADC identity tokens via ``google-auth``.

    ``fetcher`` replaces the blocking mint call for tests; by default it is the
    lazy ``google-auth`` path. The blocking call runs under
    :func:`asyncio.to_thread`. A non-Mango-Mas failure is wrapped as
    :class:`~mangomas.errors.ConfigError` carrying the exception class name only.
    """

    def __init__(self, *, fetcher: Callable[[str], str] | None = None) -> None:
        self._fetcher = fetcher or _fetch_google_id_token

    async def fetch(self, audience: str) -> tuple[str, float]:
        try:
            token = await asyncio.to_thread(self._fetcher, audience)
        except MangomasError:
            raise
        except Exception as exc:
            raise ConfigError(
                "could not mint a Google identity token", detail=type(exc).__name__
            ) from None
        return token, id_token_expiry(token)


__all__ = [
    "GOOGLE_AUTH_INSTALL_HINT",
    "GoogleIdTokenProvider",
    "IdTokenProvider",
    "id_token_expiry",
]
