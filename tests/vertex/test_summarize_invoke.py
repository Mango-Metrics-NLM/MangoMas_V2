"""Vertex E2E — scenario 5: summarize agent over persisted history."""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi import FastAPI

from mangomas.core import Orchestrator
from mangomas.core.agent import AgentRequest, AgentResponse, Message
from tests.constants import ASGI_TEST_BASE_URL, HTTPX_REQUEST_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)
pytestmark = pytest.mark.vertex


async def test_summarize_invokes_over_persisted_turns(
    vertex_app: FastAPI,
    vertex_orchestrator: Orchestrator,
) -> None:
    # Pre-populate the repo with two prior turns so summarize has context.
    repo = vertex_orchestrator.context.repo
    assert repo is not None
    await repo.save_turn(
        "chat",
        AgentRequest(messages=[Message(role="user", content="What's 2+2?")]),
        AgentResponse(content="4", agent="chat"),
    )
    await repo.save_turn(
        "chat",
        AgentRequest(messages=[Message(role="user", content="And 3+3?")]),
        AgentResponse(content="6", agent="chat"),
    )

    transport = httpx.ASGITransport(app=vertex_app)
    async with httpx.AsyncClient(transport=transport, base_url=ASGI_TEST_BASE_URL) as client:
        response = await client.post(
            "/agents/summarize/invoke",
            json={"messages": [{"role": "user", "content": "Summarise the recent thread."}]},
            timeout=HTTPX_REQUEST_TIMEOUT_SECONDS,
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["agent"] == "summarize"
    assert isinstance(body["content"], str)
    assert body["content"]

    history = await repo.list_turns(limit=5)
    assert any(turn["agent"] == "summarize" for turn in history)
