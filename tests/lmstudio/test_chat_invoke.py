"""LM Studio E2E — scenario 2: chat happy path via ``POST /agents/chat/invoke``.

Requires a live LM Studio server on the URL/model configured via
``LMSTUDIO_BASE_URL`` / ``LMSTUDIO_MODEL`` env vars. Skipped unless
``RUN_LMSTUDIO=1``.
"""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi import FastAPI

from mangomas.core import Orchestrator
from tests.constants import ASGI_TEST_BASE_URL, HTTPX_REQUEST_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


@pytest.mark.lmstudio
async def test_chat_invoke_against_live_lmstudio(
    lmstudio_app: FastAPI,
    lmstudio_orchestrator: Orchestrator,
) -> None:
    """End-to-end: a real LM Studio completion flows through invoke and persists."""
    transport = httpx.ASGITransport(app=lmstudio_app)
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
    assert body["content"], "chat response content must be non-empty"

    # Persisted turn — the orchestrator's repo should contain at least one entry.
    repo = lmstudio_orchestrator.context.repo
    assert repo is not None, "lmstudio_orchestrator must wire a repository"
    history = await repo.list_turns(limit=5)
    assert len(history) >= 1
    assert history[0]["agent"] == "chat"
