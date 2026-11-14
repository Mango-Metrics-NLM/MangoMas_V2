"""Vertex AI embedding adapter — isolated ``google.genai.Client`` embeddings.

The ``google.genai`` SDK import is deferred to :meth:`VertexEmbeddingClient.__init__`
so the module is always importable without the ``vertex`` extra. A ``client``
(an object exposing ``get_embeddings_async``) may be injected for tests; when
supplied no SDK import is performed. Each adapter constructs its own
``google.genai.Client(vertexai=True, project=..., location=...)`` rather than
mutating process-global ``vertexai.init(...)`` state. Error translation reuses
the shared qualname matrix in :mod:`mangomas.adapters._vertex_errors`.

Authentication is **Application Default Credentials only** — there is no
service-account-JSON path. Deployments authenticate via ambient identity
(Workload Identity Federation / ADC), and secret values are never logged.
"""

from __future__ import annotations

import logging
from typing import Any

from mangomas.adapters._genai import close_client_handle, close_genai_client
from mangomas.adapters._vertex_errors import translate_vertex_error
from mangomas.adapters.embeddings._shared import SingleTextEmbedMixin
from mangomas.config import DEFAULT_VERTEX_LOCATION
from mangomas.errors import LLMBadResponse

logger = logging.getLogger(__name__)

__all__ = ["VertexEmbeddingClient"]

_SDK_INSTALL_HINT = (
    "Vertex AI SDK is not installed. Install the optional extra: pip install 'mangomas[vertex]'"
)


class _GenAIEmbeddingAdapter:
    """Expose ``get_embeddings_async`` and ``aclose`` over ``google.genai.Client``."""

    def __init__(self, client: Any, *, model: str) -> None:
        self._client = client
        self._model = model

    async def get_embeddings_async(self, texts: list[str]) -> list[Any]:
        response = await self._client.aio.models.embed_content(
            model=self._model,
            contents=texts,
        )
        return list(getattr(response, "embeddings", None) or [])

    async def aclose(self) -> None:
        await close_genai_client(self._client)


def _lazy_init_model(  # pragma: no cover - requires vertex extra
    *,
    project_id: str | None,
    location: str,
    model: str,
) -> Any:
    """Initialise an isolated ``google.genai.Client`` (ADC) for embeddings.

    Excluded from coverage because the success path requires the optional
    ``vertex`` extra and live ADC; the injected-client path used by unit tests
    bypasses this helper.
    """
    try:
        # Lazy: optional ``vertex`` extra; deferring keeps the module importable
        # without the SDK installed.
        import google.genai  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(_SDK_INSTALL_HINT) from exc
    client = google.genai.Client(vertexai=True, project=project_id, location=location)
    return _GenAIEmbeddingAdapter(client, model=model)


class VertexEmbeddingClient(SingleTextEmbedMixin):
    """Embedding client backed by isolated ``google.genai.Client`` (ADC auth)."""

    def __init__(
        self,
        *,
        project_id: str | None,
        location: str = DEFAULT_VERTEX_LOCATION,
        model: str,
        client: Any | None = None,
    ) -> None:
        self._project_id = project_id
        self._location = location
        self._model = model
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = _lazy_init_model(
                project_id=project_id,
                location=location,
                model=model,
            )
            self._owns_client = True

    async def aclose(self) -> None:
        """Close owned SDK client resources when no test client was injected."""
        if self._owns_client:
            await close_client_handle(self._client)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Call ``get_embeddings_async`` and return one ``list[float]`` per text."""
        try:
            embeddings = await self._client.get_embeddings_async(texts)
        except Exception as exc:
            logger.error(
                "Vertex embeddings request failed",
                extra={
                    "event": "vertex_embedding_error",
                    "model": self._model,
                    "project": self._project_id,
                    "error_type": type(exc).__name__,
                },
            )
            raise translate_vertex_error(exc, project=self._project_id) from exc
        vectors = [[float(x) for x in emb.values] for emb in embeddings]
        if len(vectors) != len(texts):
            # Downstream zips vectors against texts by position; a short reply
            # would silently attach the wrong vector to a chunk.
            logger.error(
                "Vertex embeddings count mismatch",
                extra={
                    "event": "vertex_embedding_count_mismatch",
                    "model": self._model,
                    "expected": len(texts),
                    "received": len(vectors),
                },
            )
            raise LLMBadResponse(
                "Vertex embeddings response count does not match the request",
                detail=f"expected {len(texts)} vectors, received {len(vectors)}",
            )
        return vectors
