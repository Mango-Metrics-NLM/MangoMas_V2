"""Vertex E2E — scenario 4: buffered-completion fallback when the LLM
client does not implement :class:`StreamingLLMClient`.

Uses :meth:`Registry.scoped` against :data:`llm_registry` to swap the
``vertex`` factory for a wrapper that exposes only ``complete``, ``ping``,
``aclose`` — forcing ``ChatAgent._do_stream`` onto its buffered-fallback
branch.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest

from mangomas.adapters.llm.vertex import VertexLLMClient
from mangomas.api.app import create_app
from mangomas.composition import build_orchestrator, llm_registry
from mangomas.config import (
    DEFAULT_VERTEX_MAX_OUTPUT_TOKENS,
    LLMSettings,
)
from mangomas.core.agent import Message
from tests.constants import ASGI_TEST_BASE_URL, HTTPX_REQUEST_TIMEOUT_SECONDS
from tests.lmstudio.conftest import parse_sse_data
from tests.vertex.conftest import make_vertex_settings, orchestrator_cleanup

logger = logging.getLogger(__name__)
pytestmark = pytest.mark.vertex


class NonStreamingVertexClient:
    """Thin wrapper over :class:`VertexLLMClient` that hides ``stream()``."""

    def __init__(self, inner: VertexLLMClient) -> None:
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


def _non_streaming_vertex_factory(cfg: LLMSettings) -> NonStreamingVertexClient:
    if cfg.project is None:
        raise RuntimeError("vertex project missing in test setup")
    return NonStreamingVertexClient(
        VertexLLMClient(
            project=cfg.project,
            location=cfg.location,
            model=cfg.model,
            request_timeout_seconds=cfg.timeout_seconds,
            default_temperature=cfg.temperature,
            max_output_tokens=DEFAULT_VERTEX_MAX_OUTPUT_TOKENS,
        )
    )


async def test_stream_fallback_warns_and_delivers_buffered_content(
    vertex_project: str,
    vertex_location: str,
    vertex_model: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = make_vertex_settings(vertex_project, vertex_location, vertex_model)
    token_frames: list[dict[str, Any]] = []
    done_seen = False

    with llm_registry.scoped("vertex", _non_streaming_vertex_factory):
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
                                {"role": "user", "content": "Reply with a single short word."}
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

    assert done_seen
    assert token_frames
    assert any("complete() fallback" in rec.message for rec in caplog.records)
