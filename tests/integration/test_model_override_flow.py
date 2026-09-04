"""Tier-1 flow I6: ``MANGOMAS_AGENTS__<NAME>__MODEL_OVERRIDE`` end to end (spec-0028).

Spec-0028's unit tests prove ``build_agent_llm_overrides`` picks the right
models and that ``resolve_llm`` returns the right client. What they cannot
show is the thing an operator sets the variable for: that a request arriving
at ``POST /agents/chat/invoke`` is answered by the **override** client and not
the shared one, and that both are released on shutdown.

The oracle observes *construction*, not just ``ctx.llm``. That matters:
``build_agent_llm_overrides`` resolves the same ``llm_registry`` factory that
builds the shared client, swapping only ``cfg.model`` — so from the registry's
side the two are indistinguishable except by the model they were built with.
Recording at the factory is the only vantage point that sees both, which is
why ``recording_llm_factory`` keys on the model id.
"""

from __future__ import annotations

import pytest

from mangomas.core import Orchestrator
from tests.constants import (
    CHAT_MODEL_OVERRIDE_ENV,
    DEFAULT_LLM_MODEL,
    OVERRIDE_MODEL_ID,
    STUB_REPLY,
)
from tests.fakes import FakeLLM
from tests.integration.conftest import ComposeFn

pytestmark = pytest.mark.integration

_INVOKE_ROUTE = "/agents/chat/invoke"
_BODY = {"messages": [{"role": "user", "content": "hello"}]}
_OVERRIDE_REPLY = "answered by the override client"


async def test_override_client_answers_and_the_base_client_does_not(
    compose_app: ComposeFn,
) -> None:
    """The agent's own client serves the request; the shared one stays idle.

    Mutation proof: making ``agents/_prompt.py::resolve_llm`` ignore
    ``ctx.extras`` sends the call to the base client and fails this test.

    Both halves are asserted, because either alone is satisfiable by a bug:
    "the override was called" passes if *both* were called, and "the base was
    not called" passes if nothing was.
    """
    composed = compose_app(
        {CHAT_MODEL_OVERRIDE_ENV: OVERRIDE_MODEL_ID},
        per_model={
            DEFAULT_LLM_MODEL: FakeLLM(reply=STUB_REPLY),
            OVERRIDE_MODEL_ID: FakeLLM(reply=_OVERRIDE_REPLY),
        },
    )

    async with composed.client() as client:
        response = await client.post(_INVOKE_ROUTE, json=_BODY)

    assert response.status_code == 200, response.text
    assert response.json()["content"] == _OVERRIDE_REPLY
    assert composed.clients.called_models() == {OVERRIDE_MODEL_ID}
    # Both clients were built — the override is additional to the shared one,
    # not a replacement for it.
    assert set(composed.clients.by_model) == {DEFAULT_LLM_MODEL, OVERRIDE_MODEL_ID}


async def test_without_an_override_only_the_shared_client_exists(
    compose_app: ComposeFn,
) -> None:
    """Default-off: one client, and it is the one that answers.

    Spec-0028's backwards-compatibility claim is that no agent opts in today,
    so the extras dict is empty and behaviour is byte-identical. Asserting the
    *client count* is what makes that concrete — a bug that always built an
    override client would pass a content-only assertion.
    """
    composed = compose_app(llm=FakeLLM(reply=STUB_REPLY))

    async with composed.client() as client:
        response = await client.post(_INVOKE_ROUTE, json=_BODY)

    assert response.json()["content"] == STUB_REPLY
    assert set(composed.clients.by_model) == {DEFAULT_LLM_MODEL}
    assert composed.orchestrator.context.extras.get("agent_llm_overrides") == {}


async def test_aclose_releases_the_override_client_too(
    compose_app: ComposeFn,
    closing_orchestrators: list[Orchestrator],
) -> None:
    """Shutdown closes both clients, via the real ``aclose`` path.

    The override clients are closed by ``_AgentLLMOverrideCloseMixin``
    extending ``_close_hooks`` from the composition layer. A leak here is
    invisible in a test run and expensive in production — an httpx pool per
    overridden agent, never released.
    """
    composed = compose_app(
        {CHAT_MODEL_OVERRIDE_ENV: OVERRIDE_MODEL_ID},
        per_model={
            DEFAULT_LLM_MODEL: FakeLLM(reply=STUB_REPLY),
            OVERRIDE_MODEL_ID: FakeLLM(reply=_OVERRIDE_REPLY),
        },
    )
    closing_orchestrators.append(composed.orchestrator)

    assert not any(client.closed for client in composed.clients.by_model.values())

    await composed.orchestrator.aclose()

    assert all(client.closed for client in composed.clients.by_model.values()), (
        "aclose must release the override client alongside ctx.llm"
    )
