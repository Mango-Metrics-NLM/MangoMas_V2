"""LM Studio E2E — scenario 5: summarize agent through the public API.

Invokes ``/agents/summarize/invoke`` with a multi-message thread and asserts
that the response persists with ``agent=summarize``. Skipped unless
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
async def test_summarize_invoke_persists_with_summarize_agent(
    lmstudio_app: FastAPI,
    lmstudio_orchestrator: Orchestrator,
) -> None:
    thread = [
        {"role": "user", "content": "We need to decide on a database."},
        {"role": "assistant", "content": "Postgres or SQLite are both reasonable."},
        {"role": "user", "content": "Let's go with SQLite for local dev."},
        {"role": "assistant", "content": "Agreed — SQLite for local, Postgres for prod later."},
    ]
    transport = httpx.ASGITransport(app=lmstudio_app)
    async with httpx.AsyncClient(transport=transport, base_url=ASGI_TEST_BASE_URL) as client:
        response = await client.post(
            "/agents/summarize/invoke",
            json={"messages": thread},
            timeout=HTTPX_REQUEST_TIMEOUT_SECONDS,
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["agent"] == "summarize"
    assert isinstance(body["content"], str)
    assert body["content"], "summarize content must be non-empty"

    repo = lmstudio_orchestrator.context.repo
    assert repo is not None
    history = await repo.list_turns(limit=5)
    assert any(turn["agent"] == "summarize" for turn in history), "summarize turn must be persisted"
