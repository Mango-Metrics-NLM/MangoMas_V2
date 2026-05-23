"""LM Studio E2E — scenario 4: buffered-completion fallback when the LLM
client does not implement :class:`StreamingLLMClient`.

Uses :meth:`Registry.scoped` against :data:`mangomas.composition.llm_registry`
to register a ``NonStreamingLMStudioClient`` wrapper (which delegates
``complete()`` but exposes no ``stream()`` method) for the duration of the
test. The ChatAgent's ``_do_stream`` should fall back to a single buffered
chunk and emit the warning log line.

Skipped unless ``RUN_LMSTUDIO=1``.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest

from mangomas.adapters.llm.lmstudio import LMStudioClient
from mangomas.api.app import create_app
from mangomas.composition import build_orchestrator, llm_registry
from mangomas.config import LLMSettings
from mangomas.core.agent import Message
from tests.constants import ASGI_TEST_BASE_URL, HTTPX_REQUEST_TIMEOUT_SECONDS
from tests.lmstudio.conftest import (
    make_lmstudio_settings,
    orchestrator_cleanup,
    parse_sse_data,
)

logger = logging.getLogger(__name__)


class NonStreamingLMStudioClient:
    """Thin wrapper over :class:`LMStudioClient` that hides ``stream()``.

    Exposes only ``complete``, ``ping``, and ``aclose`` so it satisfies
    :class:`~mangomas.adapters.llm.base.LLMClient` but NOT
    :class:`~mangomas.adapters.llm.base.StreamingLLMClient`. This forces
    ChatAgent._do_stream() onto the buffered-fallback branch.
    """

    def __init__(self, inner: LMStudioClient) -> None:
        self._inner = inner

    async def complete(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
    ) -> str:
        return await self._inner.complete(messages, temperature=temperature)

    async def ping(self) -> None:
        await self._inner.ping()

    async def aclose(self) -> None:
        await self._inner.aclose()


def _non_streaming_factory(cfg: LLMSettings) -> NonStreamingLMStudioClient:
    return NonStreamingLMStudioClient(
        LMStudioClient(
            base_url=cfg.base_url,
            model=cfg.model,
            api_key=cfg.api_key,
            timeout_seconds=cfg.timeout_seconds,
            default_temperature=cfg.temperature,
        )
    )


@pytest.mark.lmstudio
async def test_stream_fallback_warns_and_delivers_buffered_content(
    lmstudio_base_url: str,
    lmstudio_model: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = make_lmstudio_settings(lmstudio_base_url, lmstudio_model)
    token_frames: list[dict[str, Any]] = []
    done_seen = False

    with llm_registry.scoped("lmstudio", _non_streaming_factory):
        orch = build_orchestrator(settings)
        app = create_app(orchestrator=orch)
        transport = httpx.ASGITransport(app=app)
        async with orchestrator_cleanup(orch):
            with caplog.at_level(logging.WARNING, logger="mangomas.agents.chat"):
                async with (
                    httpx.AsyncClient(transport=transport, base_url=ASGI_TEST_BASE_URL) as client,
                    client.stream(
                        "POST",
                        "/agents/chat/stream",
                        json={
                            "messages": [
                                {"role": "user", "content": "Reply with a single short sentence."}
                            ]
                        },
                        timeout=HTTPX_REQUEST_TIMEOUT_SECONDS,
                    ) as response,
                ):
                    assert response.status_code == 200, response.reason_phrase
                    async for line in response.aiter_lines():
                        frame = parse_sse_data(line)
                        if frame is None:
                            continue
                        if frame.get("event") == "token":
                            token_frames.append(frame)
                        elif frame.get("event") == "done":
                            done_seen = True
                            break

    assert done_seen, "fallback path must still emit the done sentinel"
    assert token_frames, "fallback path must deliver buffered content as a token frame"
    fallback_messages = [
        rec.message for rec in caplog.records if "complete() fallback" in rec.message
    ]
    assert fallback_messages, "expected the buffered-fallback warning to be emitted"
