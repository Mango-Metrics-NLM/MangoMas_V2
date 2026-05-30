"""Vertex AI embedding adapter — ``vertexai.language_models.TextEmbeddingModel``.

The ``vertexai`` SDK import is deferred to :meth:`VertexEmbeddingClient.__init__`
so the module is always importable without the ``vertex`` extra. A ``client``
(an object exposing ``get_embeddings_async``) may be injected for tests; when
supplied no SDK import is performed. Error translation reuses the shared
qualname-based matrix in :mod:`mangomas.adapters._vertex_errors`.

Authentication is **Application Default Credentials only** — there is no
service-account-JSON path. Deployments authenticate via ambient identity
(Workload Identity Federation / ADC), and secret values are never logged.
"""

from __future__ import annotations

import logging
from typing import Any

from mangomas.adapters._vertex_errors import translate_vertex_error
from mangomas.config import DEFAULT_VERTEX_LOCATION

logger = logging.getLogger(__name__)

__all__ = ["VertexEmbeddingClient"]

_SDK_INSTALL_HINT = (
    "Vertex AI SDK is not installed. Install the optional extra: pip install 'mangomas[vertex]'"
)


def _lazy_init_model(  # pragma: no cover - requires vertex extra
    *,
    project_id: str | None,
    location: str,
    model: str,
) -> Any:
    """Initialise ``vertexai`` (ADC) and return a ``TextEmbeddingModel``.

    Excluded from coverage because the success path requires the optional
    ``vertex`` extra and live ADC; the injected-client path used by unit tests
    bypasses this helper.
    """
    try:
        # Lazy: optional ``vertex`` extra; deferring keeps the module importable
        # without the SDK installed.
        import vertexai  # noqa: PLC0415
        from vertexai.language_models import TextEmbeddingModel  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(_SDK_INSTALL_HINT) from exc
    vertexai.init(project=project_id, location=location)
    return TextEmbeddingModel.from_pretrained(model)


class VertexEmbeddingClient:
    """Embedding client backed by Vertex AI ``TextEmbeddingModel`` (ADC auth)."""

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
        else:
            self._client = _lazy_init_model(
                project_id=project_id,
                location=location,
                model=model,
            )

    async def embed(self, text: str) -> list[float]:
        """Return the embedding vector for a single ``text``."""
        vectors = await self.embed_batch([text])
        return vectors[0]

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
        return [[float(x) for x in emb.values] for emb in embeddings]

    async def aclose(self) -> None:
        """Vertex SDK manages its own transport; satisfies the protocol."""
        return None
