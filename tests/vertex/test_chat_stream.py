"""Vertex AI E2E — streaming chat happy path via ``POST /agents/chat/stream``.

Verifies that token frames arrive incrementally followed by a ``done`` sentinel.
Skipped unless ``RUN_VERTEX=1``.
"""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi import FastAPI

from tests.constants import ASGI_TEST_BASE_URL, HTTPX_REQUEST_TIMEOUT_SECONDS
from tests.lmstudio.conftest import parse_sse_data

logger = logging.getLogger(__name__)


@pytest.mark.vertex
async def test_chat_stream_against_live_vertex(vertex_app: FastAPI) -> None:
    """SSE stream emits at least one ``token`` frame and a final ``done`` frame."""
    transport = httpx.ASGITransport(app=vertex_app)
    async with (
        httpx.AsyncClient(transport=transport, base_url=ASGI_TEST_BASE_URL) as client,
        client.stream(
            "POST",
            "/agents/chat/stream",
            json={"messages": [{"role": "user", "content": "Reply with the single word 'pong'."}]},
            timeout=HTTPX_REQUEST_TIMEOUT_SECONDS,
        ) as response,
    ):
        assert response.status_code == 200, await response.aread()
        events: list[dict[str, object]] = []
        async for line in response.aiter_lines():
            payload = parse_sse_data(line)
            if payload is not None:
                events.append(payload)

    token_events = [e for e in events if e.get("event") == "token"]
    done_events = [e for e in events if e.get("event") == "done"]
    assert token_events, f"expected at least one token frame; got {events!r}"
    assert done_events, f"expected a done sentinel; got {events!r}"
