"""Shared httpx → typed-error translation for OpenAI-compatible HTTP adapters.

Both the chat (:mod:`mangomas.adapters.llm.lmstudio`) and embedding
(:mod:`mangomas.adapters.embeddings.lmstudio`) LM Studio adapters speak the same
OpenAI-compatible HTTP dialect and therefore fail in the same ways. This module
hosts the single ``httpx`` exception → :class:`~mangomas.errors.LLMError`
mapping so the two adapters cannot drift apart.

The caller supplies a human-readable ``label`` (woven into the message) and the
concrete ``bad_response`` class to raise for HTTP status errors, so each adapter
keeps its own distinguishable error type without duplicating the dispatch logic.

Document parsers (spec-0035) fail differently and must say less, so they get
their own pair of translators here rather than a flag on the LLM one: a 401/403
is a :class:`~mangomas.errors.ConfigError` (bad credentials are configuration),
everything else is a :class:`~mangomas.errors.DocumentParseError`, and the
``detail`` carries only a status code or an exception class name — never the
exception text, which for a parser can echo a URL or an upstream body.
"""

from __future__ import annotations

import logging

import httpx

from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE
from mangomas.errors import (
    ConfigError,
    DocumentParseError,
    LLMBadResponse,
    LLMTimeout,
    LLMUnavailable,
    MangomasError,
)

logger = logging.getLogger(__name__)

# HTTP statuses a parser upstream uses to reject the caller's credentials.
PARSER_CREDENTIAL_STATUSES: frozenset[int] = frozenset({401, 403})


def translate_httpx_error(
    exc: BaseException,
    *,
    base_url: str,
    label: str,
    bad_response: type[LLMBadResponse],
) -> Exception:
    """Map a raw ``httpx`` exception to the typed Mango-Mas error vocabulary.

    ``label`` names the upstream (e.g. ``"LM Studio"``) for the message; the
    ``bad_response`` class is raised for HTTP status errors so callers retain a
    distinguishable error subtype. Untrusted exception text is truncated to
    :data:`~mangomas.config.DEFAULT_ERROR_DETAIL_TRUNCATE` so a remote stack
    trace can never blow out a log line or response body.
    """
    limit = DEFAULT_ERROR_DETAIL_TRUNCATE
    if isinstance(exc, httpx.TimeoutException):
        return LLMTimeout(
            f"{label} request timed out at {base_url}",
            detail=str(exc)[:limit],
        )
    if isinstance(exc, httpx.HTTPStatusError):
        return bad_response(
            f"{label} returned HTTP {exc.response.status_code}",
            detail=str(exc)[:limit],
        )
    return LLMUnavailable(
        f"{label} unreachable at {base_url}",
        detail=f"{type(exc).__name__}: {exc}"[:limit],
    )


def translate_parser_status(status_code: int, *, label: str) -> MangomasError:
    """Map a non-2xx parser response status to a typed error.

    401/403 → :class:`~mangomas.errors.ConfigError` with the fixed message
    ``"<label> rejected credentials"``; any other status →
    :class:`~mangomas.errors.DocumentParseError`. The response body is never
    consulted, so an upstream error page cannot reach a log or an envelope.
    """
    detail = f"status_code={status_code}"
    if status_code in PARSER_CREDENTIAL_STATUSES:
        return ConfigError(f"{label} rejected credentials", detail=detail)
    return DocumentParseError(f"{label} returned HTTP {status_code}", detail=detail)


def translate_httpx_parse_error(exc: httpx.HTTPError, *, label: str) -> MangomasError:
    """Map a raw ``httpx`` exception raised by a parser call to a typed error.

    A status error is delegated to :func:`translate_parser_status`; timeouts and
    every other transport failure become
    :class:`~mangomas.errors.DocumentParseError`. ``detail`` is the exception
    class name only — ``str(exc)`` is deliberately not used.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return translate_parser_status(exc.response.status_code, label=label)
    kind = "timed out" if isinstance(exc, httpx.TimeoutException) else "failed"
    return DocumentParseError(f"{label} request {kind}", detail=type(exc).__name__)
