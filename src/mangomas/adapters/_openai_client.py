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
Both are enforced at subclass-creation time by :meth:`__init_subclass__` — a
subclass that forgets one fails at import rather than with an ``AttributeError``
raised from inside an upstream-failure handler, where it would mask the real
network error.

Every parameter past ``model`` is keyword-only: the two subclasses forward to
``super().__init__`` by keyword, so inserting or reordering a base parameter
can never silently rebind a subclass's arguments.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

import httpx

from mangomas.adapters._http_errors import translate_httpx_error

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.errors import LLMBadResponse

logger = logging.getLogger(__name__)

_REQUIRED_CLASS_ATTRS = ("_LABEL", "_BAD_RESPONSE")


class OpenAICompatHTTPClient:
    """Own the ``httpx.AsyncClient`` lifecycle for an OpenAI-compatible upstream."""

    _LABEL: ClassVar[str]
    _BAD_RESPONSE: ClassVar[type[LLMBadResponse]]

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Fail loud at import when a subclass omits the required class attributes."""
        super().__init_subclass__(**kwargs)
        missing = [name for name in _REQUIRED_CLASS_ATTRS if not hasattr(cls, name)]
        if missing:
            raise TypeError(f"{cls.__name__} must define {', '.join(missing)}")

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
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
        # Ownership is invisible from the outside, and getting it wrong shows up
        # as a leaked socket or a double-close during lifespan teardown — the
        # one piece of lifecycle state worth naming in the log.
        logger.debug(
            "Closing OpenAI-compatible HTTP client",
            extra={
                "event": "openai_client_aclose",
                "label": self._LABEL,
                "base_url": self._base_url,
                "owns_client": self._owns_client,
            },
        )
        if self._owns_client:
            await self._client.aclose()
