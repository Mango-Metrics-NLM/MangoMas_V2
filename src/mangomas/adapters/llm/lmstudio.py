"""LM Studio adapter — OpenAI-compatible chat completions over HTTP."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

import httpx

from mangomas.core.agent import Message
from mangomas.errors import LLMBadResponse

logger = logging.getLogger(__name__)


class LMStudioError(LLMBadResponse):
    """Raised when LM Studio returns an unexpected or malformed response."""


class LMStudioClient:
    """Thin OpenAI-compatible client targeting LM Studio's local server."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "lm-studio",
        timeout_seconds: float = 60.0,
        default_temperature: float = 0.2,
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
        resp = await self._client.post(f"{self._base_url}/chat/completions", json=payload)
        resp.raise_for_status()
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
        resp = await self._client.get(f"{self._base_url}/models")
        resp.raise_for_status()
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
        async with self._client.stream(
            "POST", f"{self._base_url}/chat/completions", json=payload
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                chunk_data = line[len("data: ") :]
                if chunk_data.strip() == "[DONE]":
                    return
                try:
                    data = json.loads(chunk_data)
                    content = str(data["choices"][0]["delta"].get("content") or "")
                    if content:
                        yield content
                except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                    logger.debug("Skipping unparseable SSE chunk: %s", exc)

    async def aclose(self) -> None:
        """Close the underlying HTTP client (if owned)."""
        if self._owns_client:
            await self._client.aclose()
