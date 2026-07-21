"""LM Studio embedding adapter — OpenAI-compatible ``/v1/embeddings`` over HTTP.

Mirrors :class:`mangomas.adapters.llm.lmstudio.LMStudioClient`: ``base_url`` is
``rstrip("/")``-normalised, requests POST to ``{base_url}/embeddings`` (which
resolves to ``.../v1/embeddings`` for the default base_url that already ends in
``/v1``), and raw ``httpx`` exceptions are mapped to the typed
:class:`~mangomas.errors.LLMError` vocabulary. An ``httpx.AsyncClient`` may be
injected for tests.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from mangomas.adapters._http_errors import translate_httpx_error
from mangomas.config import DEFAULT_EMBEDDINGS_TIMEOUT_SECONDS
from mangomas.errors import LLMBadResponse

logger = logging.getLogger(__name__)

_LABEL = "LM Studio embeddings"


class LMStudioEmbeddingError(LLMBadResponse):
    """Raised when LM Studio returns an unexpected or malformed embedding response."""


def _translate_httpx_error(exc: BaseException, *, base_url: str) -> Exception:
    """Map raw ``httpx`` exceptions to typed :class:`~mangomas.errors.LLMError` subclasses."""
    return translate_httpx_error(
        exc, base_url=base_url, label=_LABEL, bad_response=LMStudioEmbeddingError
    )


class LMStudioEmbeddingClient:
    """Thin OpenAI-compatible embeddings client targeting LM Studio's local server."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "lm-studio",
        timeout_seconds: float = DEFAULT_EMBEDDINGS_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def embed(self, text: str) -> list[float]:
        """Return the embedding vector for a single ``text``."""
        vectors = await self.embed_batch([text])
        return vectors[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """POST ``/embeddings`` and return one vector per input text, in order."""
        payload: dict[str, Any] = {"model": self._model, "input": texts}
        try:
            resp = await self._client.post(f"{self._base_url}/embeddings", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error(
                "LM Studio embeddings request failed",
                extra={"error": type(exc).__name__, "base_url": self._base_url},
            )
            raise _translate_httpx_error(exc, base_url=self._base_url) from exc
        data = resp.json()
        try:
            rows = data["data"]
            return [[float(x) for x in row["embedding"]] for row in rows]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.error(
                "Malformed LM Studio embeddings response",
                extra={"response_keys": list(data.keys()) if isinstance(data, dict) else []},
            )
            raise LMStudioEmbeddingError(
                f"Malformed LM Studio embeddings response: {data!r}"
            ) from exc

    async def aclose(self) -> None:
        """Close the underlying HTTP client (if owned)."""
        if self._owns_client:
            await self._client.aclose()
