"""Integration tests for the public FastAPI boundary.

Skipped by default; set RUN_INTEGRATION=1 to run.
"""

from __future__ import annotations

import httpx
import pytest
from tests.fakes import FakeLLM, FakeRepository

from mangomas.agents import ChatAgent
from mangomas.api.app import create_app
from mangomas.core import AgentContext, Orchestrator


@pytest.mark.integration
@pytest.mark.asyncio
async def test_invoke_flow_persists_turn(fake_repo: FakeRepository) -> None:
    llm = FakeLLM(reply="integration-reply")
    orch = Orchestrator(AgentContext(llm=llm, repo=fake_repo))
    orch.register(ChatAgent())
    transport = httpx.ASGITransport(app=create_app(orchestrator=orch))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/agents/chat/invoke",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )

    assert response.status_code == 200
    assert response.json()["content"] == "integration-reply"
    turns = await fake_repo.list_turns()
    assert len(turns) == 1
    assert turns[0]["agent"] == "chat"
