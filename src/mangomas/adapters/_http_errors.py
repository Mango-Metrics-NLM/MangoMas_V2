"""Shared httpx → typed-error translation for OpenAI-compatible HTTP adapters.

Both the chat (:mod:`mangomas.adapters.llm.lmstudio`) and embedding
(:mod:`mangomas.adapters.embeddings.lmstudio`) LM Studio adapters speak the same
OpenAI-compatible HTTP dialect and therefore fail in the same ways. This module
hosts the single ``httpx`` exception → :class:`~mangomas.errors.LLMError`
mapping so the two adapters cannot drift apart.

The caller supplies a human-readable ``label`` (woven into the message) and the
concrete ``bad_response`` class to raise for HTTP status errors, so each adapter
keeps its own distinguishable error type without duplicating the dispatch logic.
"""

from __future__ import annotations

import logging

import httpx

from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE
from mangomas.errors import LLMBadResponse, LLMTimeout, LLMUnavailable

logger = logging.getLogger(__name__)


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
