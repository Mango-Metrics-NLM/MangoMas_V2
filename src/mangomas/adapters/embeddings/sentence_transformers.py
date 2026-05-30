"""In-process embedding adapter backed by ``sentence-transformers``.

The heavy ``sentence_transformers`` import is deferred to ``__init__`` (guarded
with ``# noqa: PLC0415``) so this module is always importable without the
optional ``embeddings-local`` extra. A pre-built ``model`` may be injected for
tests, in which case no import is performed. Encoding is synchronous CPU/GPU
work, so it runs inside ``asyncio.to_thread`` per the project's async-I/O rule.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_SDK_INSTALL_HINT = (
    "sentence-transformers is not installed. Install the optional extra: "
    "pip install 'mangomas[embeddings-local]'"
)


def _lazy_load_model(model_name: str) -> Any:  # pragma: no cover - requires extra
    """Load a ``SentenceTransformer`` lazily.

    Excluded from coverage because the success path requires the optional
    ``embeddings-local`` extra; the injected-model path (used by unit tests)
    bypasses this helper entirely.
    """
    try:
        # Lazy: heavy optional dependency; importing at module load would force
        # it on every user, defeating the optional-extra contract.
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(_SDK_INSTALL_HINT) from exc
    return SentenceTransformer(model_name)


class SentenceTransformersEmbeddingClient:
    """Embedding client using an in-process ``SentenceTransformer`` model."""

    def __init__(self, model: str, *, client: Any | None = None) -> None:
        self._model_name = model
        self._model = client if client is not None else _lazy_load_model(model)

    async def embed(self, text: str) -> list[float]:
        """Return the embedding vector for a single ``text``."""
        vectors = await self.embed_batch([text])
        return vectors[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Encode ``texts`` off the event loop and return plain ``list[float]`` vectors."""
        return await asyncio.to_thread(self._encode, texts)

    def _encode(self, texts: list[str]) -> list[list[float]]:
        result = self._model.encode(texts)
        return [[float(x) for x in row] for row in result]

    async def aclose(self) -> None:
        """No owned sockets; satisfies the ``EmbeddingClient`` protocol."""
        return None
