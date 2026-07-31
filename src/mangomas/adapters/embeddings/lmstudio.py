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

from mangomas.adapters._openai_client import OpenAICompatHTTPClient
from mangomas.adapters.embeddings._shared import SingleTextEmbedMixin
from mangomas.config import DEFAULT_EMBEDDINGS_TIMEOUT_SECONDS, DEFAULT_ERROR_DETAIL_TRUNCATE
from mangomas.errors import LLMBadResponse

logger = logging.getLogger(__name__)


class LMStudioEmbeddingError(LLMBadResponse):
    """Raised when LM Studio returns an unexpected or malformed embedding response."""


class LMStudioEmbeddingClient(SingleTextEmbedMixin, OpenAICompatHTTPClient):
    """Thin OpenAI-compatible embeddings client targeting LM Studio's local server."""

    _LABEL = "LM Studio embeddings"
    _BAD_RESPONSE = LMStudioEmbeddingError

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "lm-studio",
        timeout_seconds: float = DEFAULT_EMBEDDINGS_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            base_url,
            model,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            client=client,
        )

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
            raise self._translate_error(exc) from exc
        data = resp.json()
        try:
            rows = data["data"]
            return [[float(x) for x in row["embedding"]] for row in rows]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.error(
                "Malformed LM Studio embeddings response",
                extra={"response_keys": list(data.keys()) if isinstance(data, dict) else []},
            )
            # Keep the client-visible message static; the (truncated) body goes
            # into ``detail`` so an arbitrarily large upstream payload can never
            # blow out an error envelope or log line (spec 0014 / D2).
            raise LMStudioEmbeddingError(
                "Malformed LM Studio embeddings response",
                detail=repr(data)[:DEFAULT_ERROR_DETAIL_TRUNCATE],
            ) from exc
