"""LM Studio E2E — scenario 3: streaming happy path via ``POST /agents/chat/stream``.

Asserts that at least one ``event: token`` frame is delivered and that the
final ``event: done`` sentinel is emitted. Skipped unless ``RUN_LMSTUDIO=1``.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from tests.constants import ASGI_TEST_BASE_URL
from tests.lmstudio.conftest import parse_sse_data

logger = logging.getLogger(__name__)


@pytest.mark.lmstudio
async def test_chat_stream_emits_token_and_done_frames(
    lmstudio_app: FastAPI,
    lmstudio_client_timeout: float,
) -> None:
    transport = httpx.ASGITransport(app=lmstudio_app)
    token_frames: list[dict[str, Any]] = []
    done_seen = False

    async with (
        httpx.AsyncClient(transport=transport, base_url=ASGI_TEST_BASE_URL) as client,
        client.stream(
            "POST",
            "/agents/chat/stream",
            json={"messages": [{"role": "user", "content": "Stream a short reply."}]},
            timeout=lmstudio_client_timeout,
        ) as response,
    ):
        assert response.status_code == 200, response.reason_phrase
        assert response.headers["content-type"].startswith("text/event-stream")

        async for line in response.aiter_lines():
            frame = parse_sse_data(line)
            if frame is None:
                continue
            if frame.get("event") == "token":
                token_frames.append(frame)
            elif frame.get("event") == "done":
                done_seen = True
                break

    assert token_frames, "expected at least one token frame from SSE stream"
    assert done_seen, "expected SSE stream to terminate with an event=done frame"
    # Tokens must carry content (non-empty string).
    assert any(f["data"]["content"] for f in token_frames), (
        "at least one token frame must have non-empty content"
    )
