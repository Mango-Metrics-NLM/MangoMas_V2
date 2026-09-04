"""Tier-1 baseline: the invoke path through the real composition root.

This was the whole of ``tests/integration/`` before spec-0029, and it built
its own ``Orchestrator`` by hand — which made it a slower copy of
``tests/test_api.py`` rather than an integration test. It now runs through
``build_orchestrator``, so it proves the thing the other tests cannot: that
the default configuration composes into a working request path.

Skipped by default; set ``RUN_INTEGRATION=1`` (CI does, on every push, via
``make gated-suites``).
"""

from __future__ import annotations

import pytest

from tests.constants import STUB_REPLY
from tests.fakes import FakeLLM
from tests.integration.conftest import ComposeFn, read_history, turn_content

pytestmark = pytest.mark.integration


async def test_invoke_flow_persists_turn(compose_app: ComposeFn) -> None:
    """A default-configured app answers an invoke and persists the turn."""
    composed = compose_app(llm=FakeLLM(reply=STUB_REPLY))

    async with composed.client() as client:
        response = await client.post(
            "/agents/chat/invoke",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )

    assert response.status_code == 200, response.text
    assert response.json()["content"] == STUB_REPLY

    turns = await read_history(composed)
    assert len(turns) == 1
    assert turns[0]["agent"] == "chat"
    assert turn_content(turns[0]) == STUB_REPLY


async def test_unknown_agent_returns_the_typed_404_envelope(compose_app: ComposeFn) -> None:
    """The error envelope is part of the composed surface, not just the router.

    ``tests/test_errors.py`` walks the status table against hand-built
    orchestrators; this proves the mapping survives real composition — an
    unknown agent reaches ``AgentNotFound`` and comes back as a 404 envelope
    rather than a 500.
    """
    composed = compose_app(llm=FakeLLM())

    async with composed.client() as client:
        response = await client.post(
            "/agents/does-not-exist/invoke",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )

    assert response.status_code == 404, response.text
    assert response.json()["error"] == "agent_not_found"
    assert await read_history(composed) == []


async def test_composed_app_registers_the_documented_agent_roster(
    compose_app: ComposeFn,
) -> None:
    """``GET /agents`` reflects what ``composition/agents.py`` registered.

    The roster is a user-facing surface (the CLI's ``mangomas agents`` reads
    the same registry). Asserting it here catches an agent that stopped being
    registered at composition time — which no unit test would notice, because
    each one registers its own agents explicitly.
    """
    composed = compose_app(llm=FakeLLM())

    async with composed.client() as client:
        response = await client.get("/agents")

    assert response.status_code == 200, response.text
    assert set(response.json()["agents"]) >= {"chat", "summarize", "tool", "planner", "reviewer"}
