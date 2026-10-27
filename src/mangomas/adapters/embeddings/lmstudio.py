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
from mangomas.config import DEFAULT_EMBEDDINGS_TIMEOUT_SECONDS
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
        """POST ``/embeddings`` and return one vector per input text, in order.

        The OpenAI dialect returns ``data[]`` rows each carrying an ``index``;
        rows are placed by that index (a reordered response would otherwise be
        zipped against the wrong chunks downstream) and the count must equal
        ``len(texts)`` — a short or duplicated response is a malformed one.
        """
        payload: dict[str, Any] = {"model": self._model, "input": texts}
        resp = await self._request(
            "POST", "/embeddings", json=payload, log_event="LM Studio embeddings request failed"
        )
        # ``resp.json()`` sits inside the ``try`` so a non-JSON 200 (a proxy's
        # HTML page) surfaces as the typed error rather than an untyped 500.
        body: Any = None
        try:
            body = resp.json()
            return _vectors_in_order(body["data"], expected=len(texts))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise self._malformed_response(f"{type(exc).__name__}: {exc}", body=body) from exc


def _vectors_in_order(rows: Any, *, expected: int) -> list[list[float]]:
    """Place ``rows`` by their ``index`` field and require exactly ``expected`` of them.

    Raises ``ValueError`` (caught by the caller's malformed-response boundary)
    for a missing, duplicated or out-of-range index, or a count mismatch; a
    row without ``index`` keeps its position, as the pre-``index`` dialect did.
    """
    vectors: list[list[float] | None] = [None] * expected
    seen = 0
    for position, row in enumerate(rows):
        embedding = row["embedding"]
        index = row.get("index", position)
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < expected:
            raise ValueError(f"embedding index {index!r} is outside 0..{expected - 1}")
        if vectors[index] is not None:
            raise ValueError(f"duplicate embedding index {index}")
        vectors[index] = [float(x) for x in embedding]
        seen += 1
    if seen != expected:
        raise ValueError(f"expected {expected} embeddings, got {seen}")
    return [vector for vector in vectors if vector is not None]
