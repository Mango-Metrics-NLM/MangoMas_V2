"""LM Studio adapter — OpenAI-compatible chat completions over HTTP."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from typing import TYPE_CHECKING, Any, Final

import httpx

from mangomas.adapters._openai_client import OpenAICompatHTTPClient
from mangomas.config import DEFAULT_LLM_TEMPERATURE, DEFAULT_LLM_TIMEOUT_SECONDS
from mangomas.errors import LLMBadResponse

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core.agent import Message

logger = logging.getLogger(__name__)

# SSE sentinel that marks the end of a streaming completion. The literal is
# defined by the OpenAI-compatible streaming spec — promoted to a module-level
# constant so it is named at every reference site.
_SSE_DONE_SENTINEL: Final[str] = "[DONE]"


class LMStudioError(LLMBadResponse):
    """Raised when LM Studio returns an unexpected or malformed response."""


class LMStudioClient(OpenAICompatHTTPClient):
    """Thin OpenAI-compatible client targeting LM Studio's local server."""

    _LABEL = "LM Studio"
    _BAD_RESPONSE = LMStudioError

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "lm-studio",
        timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
        default_temperature: float = DEFAULT_LLM_TEMPERATURE,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            base_url,
            model,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            client=client,
        )
        self._default_temperature = default_temperature

    async def complete(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Call ``/chat/completions`` and return the first choice's content."""
        payload = self._payload(messages, temperature=temperature, max_tokens=max_tokens)
        resp = await self._request(
            "POST", "/chat/completions", json=payload, log_event="LM Studio request failed"
        )
        # ``resp.json()`` sits inside the ``try``: a proxy that answers 200 with
        # an HTML page is a malformed *upstream* response, not a bug in this
        # process, so it must surface as the same typed ``LMStudioError`` as a
        # JSON body of the wrong shape rather than as an untyped 500.
        body: Any = None
        try:
            body = resp.json()
            content = body["choices"][0]["message"]["content"]
        # RecursionError: the C JSON scanner's answer to a pathologically
        # nested body — still a malformed upstream response (spec-0039 D-4).
        except (KeyError, IndexError, TypeError, ValueError, RecursionError) as exc:
            raise self._malformed_response(f"{type(exc).__name__}: {exc}", body=body) from exc
        if not isinstance(content, str):
            # ``null`` content (a refusal or a tool-only turn) used to be
            # stringified to the literal ``"None"`` and handed to the caller.
            raise self._malformed_response(
                f"content is {type(content).__name__}, not str", body=body
            )
        return content

    def _payload(
        self,
        messages: list[Message],
        *,
        temperature: float | None,
        max_tokens: int | None,
        stream: bool = False,
    ) -> dict[str, Any]:
        """Build the ``/chat/completions`` request body shared by ``complete`` and ``stream``."""
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": (self._default_temperature if temperature is None else temperature),
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if stream:
            payload["stream"] = True
        return payload

    async def ping(self) -> None:
        """GET ``/models`` to verify the LM Studio server is reachable."""
        await self._request("GET", "/models", log_event="LM Studio ping failed")
        logger.debug("LM Studio ping OK (%s)", self._base_url)

    async def stream(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Return an async iterator that yields content tokens from a streaming completion."""
        return self._stream_impl(messages, temperature=temperature, max_tokens=max_tokens)

    async def _stream_impl(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[str, None]:
        """Async generator: yields SSE content tokens from LM Studio."""
        payload = self._payload(
            messages, temperature=temperature, max_tokens=max_tokens, stream=True
        )
        try:
            stream_cm = self._client.stream(
                "POST", f"{self._base_url}/chat/completions", json=payload
            )
            async with stream_cm as resp:
                try:
                    resp.raise_for_status()
                except httpx.HTTPError as status_exc:
                    self._log_and_translate(status_exc, "LM Studio stream request failed")
                async for line in resp.aiter_lines():
                    yield_value = self._parse_sse_line(line)
                    if yield_value is None:
                        continue
                    if yield_value == "":  # sentinel for [DONE]
                        return
                    yield yield_value
        except httpx.HTTPError as exc:
            self._log_and_translate(exc, "LM Studio stream request failed")

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
            delta = data["choices"][0]["delta"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, RecursionError) as exc:
            logger.debug(
                "Skipping unparseable SSE chunk: %s",
                exc,
                extra={"event": "lmstudio_sse_chunk_skipped"},
            )
            return None
        if not isinstance(delta, dict):
            # `"delta": null` or a string would otherwise raise AttributeError
            # and abort the whole stream (spec-0039 D-5).
            logger.debug(
                "Skipping SSE chunk whose delta is %s",
                type(delta).__name__,
                extra={"event": "lmstudio_sse_chunk_skipped"},
            )
            return None
        content = str(delta.get("content") or "")
        return content or None
