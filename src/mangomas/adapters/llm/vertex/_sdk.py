"""Lazy Vertex SDK imports and the adapter's typed error.

Kept off ``_client`` so the client module stays the monkeypatch seam: callers
and tests bind ``_lazy_import_vertex`` / ``_lazy_import_credentials`` on
``mangomas.adapters.llm.vertex._client``, which re-exports these names.
"""

from __future__ import annotations

from typing import Any

from mangomas.adapters._vertex_errors import translate_vertex_error
from mangomas.errors import LLMBadResponse

_SDK_INSTALL_HINT = (
    "Vertex AI SDK is not installed. Install the optional extra: pip install 'mangomas[vertex]'"
)


class VertexError(LLMBadResponse):
    """Raised when Vertex AI returns an unexpected or malformed response."""


def _translate_vertex_error(exc: BaseException, *, project: str | None) -> Exception:
    """Map a Vertex SDK exception to the typed Mango-Mas error vocabulary.

    Thin wrapper over the shared qualname matrix that supplies this adapter's
    :class:`VertexError` bad-request subtype.
    """
    return translate_vertex_error(exc, project=project, bad_request_error=VertexError)


def _lazy_import_vertex() -> tuple[Any, Any, Any]:  # pragma: no cover - requires vertex extra
    """Import ``vertexai``, ``Content``, ``Part`` lazily.

    Returns ``(vertexai_module, Content_cls, Part_cls)``. Raises
    :class:`ImportError` with an install hint when the SDK is absent.
    """
    try:
        # Lazy: the SDK is an optional extra. Importing it at module load would
        # force the dependency on every user.
        import vertexai  # noqa: PLC0415
        from vertexai.generative_models import Content, Part  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(_SDK_INSTALL_HINT) from exc
    return vertexai, Content, Part


def _lazy_import_credentials() -> Any:  # pragma: no cover - requires vertex extra
    """Import ``google.oauth2.service_account.Credentials`` lazily."""
    try:
        from google.oauth2 import service_account  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(_SDK_INSTALL_HINT) from exc
    return service_account.Credentials
