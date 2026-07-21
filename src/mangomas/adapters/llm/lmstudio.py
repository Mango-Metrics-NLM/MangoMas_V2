"""LM Studio adapter — OpenAI-compatible chat completions over HTTP."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, Final

import httpx

from mangomas.adapters._http_errors import translate_httpx_error
from mangomas.config import DEFAULT_LLM_TEMPERATURE, DEFAULT_LLM_TIMEOUT_SECONDS
from mangomas.core.agent import Message
from mangomas.errors import LLMBadResponse

logger = logging.getLogger(__name__)

_LABEL = "LM Studio"

# SSE sentinel that marks the end of a streaming completion. The literal is
# defined by the OpenAI-compatible streaming spec — promoted to a module-level
# constant so it is named at every reference site.
_SSE_DONE_SENTINEL: Final[str] = "[DONE]"


class LMStudioError(LLMBadResponse):
    """Raised when LM Studio returns an unexpected or malformed response."""


def _translate_httpx_error(exc: BaseException, *, base_url: str) -> Exception:
    """Map raw ``httpx`` exceptions to typed :class:`~mangomas.errors.LLMError` subclasses."""
    return translate_httpx_error(exc, base_url=base_url, label=_LABEL, bad_response=LMStudioError)


class LMStudioClient:
    """Thin OpenAI-compatible client targeting LM Studio's local server."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "lm-studio",
        timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
        default_temperature: float = DEFAULT_LLM_TEMPERATURE,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._default_temperature = default_temperature
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def complete(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
    ) -> str:
        """Call ``/chat/completions`` and return the first choice's content."""
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": (self._default_temperature if temperature is None else temperature),
        }
        try:
            resp = await self._client.post(f"{self._base_url}/chat/completions", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error(
                "LM Studio request failed",
                extra={"error": type(exc).__name__, "base_url": self._base_url},
            )
            raise _translate_httpx_error(exc, base_url=self._base_url) from exc
        data = resp.json()
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            logger.error(
                "Malformed LM Studio response",
                extra={"response_keys": list(data.keys()) if isinstance(data, dict) else []},
            )
            raise LMStudioError(f"Malformed LM Studio response: {data!r}") from exc

    async def ping(self) -> None:
        """GET ``/models`` to verify the LM Studio server is reachable."""
        try:
            resp = await self._client.get(f"{self._base_url}/models")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error(
                "LM Studio ping failed",
                extra={"error": type(exc).__name__, "base_url": self._base_url},
            )
            raise _translate_httpx_error(exc, base_url=self._base_url) from exc
        logger.debug("LM Studio ping OK (%s)", self._base_url)

    async def stream(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        """Return an async iterator that yields content tokens from a streaming completion."""
        return self._stream_impl(messages, temperature=temperature)

    async def _stream_impl(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
    ) -> AsyncGenerator[str, None]:
        """Async generator: yields SSE content tokens from LM Studio."""
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": (self._default_temperature if temperature is None else temperature),
            "stream": True,
        }
        try:
            stream_cm = self._client.stream(
                "POST", f"{self._base_url}/chat/completions", json=payload
            )
            async with stream_cm as resp:
                try:
                    resp.raise_for_status()
                except httpx.HTTPError as status_exc:
                    raise _translate_httpx_error(
                        status_exc, base_url=self._base_url
                    ) from status_exc
                async for line in resp.aiter_lines():
                    yield_value = self._parse_sse_line(line)
                    if yield_value is None:
                        continue
                    if yield_value == "":  # sentinel for [DONE]
                        return
                    yield yield_value
        except httpx.HTTPError as exc:
            logger.error(
                "LM Studio stream request failed",
                extra={"error": type(exc).__name__, "base_url": self._base_url},
            )
            raise _translate_httpx_error(exc, base_url=self._base_url) from exc

    @staticmethod
    def _parse_sse_line(line: str) -> str | None:
        """Return a content token, an empty string sentinel for [DONE], or None to skip."""
        if not line.startswith("data: "):
            return None
        chunk_data = line[len("data: ") :]
        if chunk_data.strip() == _SSE_DONE_SENTINEL:
            return ""
        try:
            data = json.loads(chunk_data)
            content = str(data["choices"][0]["delta"].get("content") or "")
            return content or None
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            logger.debug("Skipping unparseable SSE chunk: %s", exc)
            return None

    async def aclose(self) -> None:
        """Close the underlying HTTP client (if owned)."""
        if self._owns_client:
            await self._client.aclose()
