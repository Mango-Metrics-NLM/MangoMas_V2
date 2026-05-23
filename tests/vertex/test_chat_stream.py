"""Vertex E2E — scenario 3: streaming happy path via ``POST /agents/chat/stream``."""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from tests.constants import ASGI_TEST_BASE_URL, HTTPX_REQUEST_TIMEOUT_SECONDS
from tests.lmstudio.conftest import parse_sse_data

logger = logging.getLogger(__name__)
pytestmark = pytest.mark.vertex


async def test_chat_stream_yields_tokens_and_done_frame(vertex_app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=vertex_app)
    token_frames: list[dict[str, Any]] = []
    done_seen = False

    async with (
        httpx.AsyncClient(transport=transport, base_url=ASGI_TEST_BASE_URL) as client,
        client.stream(
            "POST",
            "/agents/chat/stream",
            json={"messages": [{"role": "user", "content": "Reply with one short word."}]},
            timeout=HTTPX_REQUEST_TIMEOUT_SECONDS,
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

    assert token_frames, "expected at least one token frame from Vertex"
    assert done_seen, "stream must terminate with a done sentinel"
    first_token = token_frames[0]
    assert first_token["data"]["content"]
    assert first_token["content"] == first_token["data"]["content"]
