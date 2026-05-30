"""Shared Vertex AI exception → typed-error translation.

Both the chat (:mod:`mangomas.adapters.llm.vertex`) and embedding
(:mod:`mangomas.adapters.embeddings.vertex`) Vertex adapters surface the same
``google.api_core`` / ``google.auth`` exception families. This module hosts the
single qualname-based translation matrix so neither adapter has to import the
other's internals (which would couple the two adapter namespaces).

Translation is qualname-based (``module.qualname``) so the matrix can be
exercised in unit tests without installing the Google SDK.
"""

from __future__ import annotations

from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE
from mangomas.errors import LLMBadResponse, LLMTimeout, LLMUnavailable


def _qualname(exc: BaseException) -> str:
    return f"{type(exc).__module__}.{type(exc).__qualname__}"


_VERTEX_TIMEOUT_TYPES: frozenset[str] = frozenset(
    {
        "google.api_core.exceptions.DeadlineExceeded",
        "google.api_core.exceptions.RetryError",
    }
)
_VERTEX_UNAVAILABLE_TYPES: frozenset[str] = frozenset(
    {
        "google.api_core.exceptions.ServiceUnavailable",
        "google.api_core.exceptions.InternalServerError",
        "google.api_core.exceptions.GatewayTimeout",
        "google.api_core.exceptions.Aborted",
        "google.auth.exceptions.RefreshError",
        "google.auth.exceptions.DefaultCredentialsError",
    }
)
_VERTEX_BAD_REQUEST_TYPES: frozenset[str] = frozenset(
    {
        "google.api_core.exceptions.InvalidArgument",
        "google.api_core.exceptions.PermissionDenied",
        "google.api_core.exceptions.Unauthenticated",
        "google.api_core.exceptions.NotFound",
        "google.api_core.exceptions.FailedPrecondition",
    }
)


def translate_vertex_error(
    exc: BaseException,
    *,
    project: str | None,
    bad_request_error: type[LLMBadResponse] = LLMBadResponse,
) -> Exception:
    """Map a Vertex SDK exception to the typed Mango-Mas error vocabulary.

    ``bad_request_error`` is raised for the client-error (4xx-equivalent) class
    of Vertex exceptions, letting each adapter supply a distinguishable subtype
    while sharing the dispatch. Untrusted exception text is truncated to
    :data:`~mangomas.config.DEFAULT_ERROR_DETAIL_TRUNCATE`.
    """
    qualname = _qualname(exc)
    detail = f"{type(exc).__name__}: {exc}"[:DEFAULT_ERROR_DETAIL_TRUNCATE]
    if qualname in _VERTEX_TIMEOUT_TYPES:
        return LLMTimeout(f"Vertex AI request timed out (project={project!r})", detail=detail)
    if qualname in _VERTEX_BAD_REQUEST_TYPES:
        return bad_request_error(
            f"Vertex AI rejected the request (project={project!r})", detail=detail
        )
    if qualname in _VERTEX_UNAVAILABLE_TYPES:
        return LLMUnavailable(f"Vertex AI unavailable (project={project!r})", detail=detail)
    return LLMUnavailable(f"Vertex AI request failed (project={project!r})", detail=detail)
