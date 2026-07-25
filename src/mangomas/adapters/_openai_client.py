"""Shared HTTP-client lifecycle for OpenAI-compatible adapters.

The chat (:mod:`mangomas.adapters.llm.lmstudio`) and embedding
(:mod:`mangomas.adapters.embeddings.lmstudio`) LM Studio adapters construct,
own, and tear down an ``httpx.AsyncClient`` in exactly the same way. This base
class hosts that lifecycle once — base-URL normalisation, bearer-auth client
construction, injected-client ownership tracking, error translation binding,
and ``aclose`` — so the two adapters cannot drift apart (the companion of
:mod:`mangomas.adapters._http_errors`, which already centralises the error
mapping itself).

Subclasses set two class attributes: ``_LABEL`` (human-readable upstream name
woven into error messages) and ``_BAD_RESPONSE`` (the adapter's distinguishable
:class:`~mangomas.errors.LLMBadResponse` subtype raised for HTTP status errors).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import httpx

from mangomas.adapters._http_errors import translate_httpx_error

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.errors import LLMBadResponse


class OpenAICompatHTTPClient:
    """Own the ``httpx.AsyncClient`` lifecycle for an OpenAI-compatible upstream."""

    _LABEL: ClassVar[str]
    _BAD_RESPONSE: ClassVar[type[LLMBadResponse]]

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def _translate_error(self, exc: BaseException) -> Exception:
        """Map a raw ``httpx`` exception to the typed ``LLMError`` vocabulary."""
        return translate_httpx_error(
            exc,
            base_url=self._base_url,
            label=self._LABEL,
            bad_response=self._BAD_RESPONSE,
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client (if owned)."""
        if self._owns_client:
            await self._client.aclose()
