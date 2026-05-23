"""Vertex E2E — scenario 2: chat happy path via ``POST /agents/chat/invoke``."""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi import FastAPI

from mangomas.core import Orchestrator
from tests.constants import ASGI_TEST_BASE_URL, HTTPX_REQUEST_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)
pytestmark = pytest.mark.vertex


async def test_chat_invoke_against_live_vertex(
    vertex_app: FastAPI,
    vertex_orchestrator: Orchestrator,
) -> None:
    transport = httpx.ASGITransport(app=vertex_app)
    async with httpx.AsyncClient(transport=transport, base_url=ASGI_TEST_BASE_URL) as client:
        response = await client.post(
            "/agents/chat/invoke",
            json={"messages": [{"role": "user", "content": "Say 'pong' and nothing else."}]},
            timeout=HTTPX_REQUEST_TIMEOUT_SECONDS,
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["agent"] == "chat"
    assert isinstance(body["content"], str)
    assert body["content"], "chat response must be non-empty"

    repo = vertex_orchestrator.context.repo
    assert repo is not None
    history = await repo.list_turns(limit=5)
    assert len(history) >= 1
    assert history[0]["agent"] == "chat"
