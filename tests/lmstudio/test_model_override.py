"""LM Studio E2E — scenario 11: per-agent ``MODEL_OVERRIDE`` live (spec-0028).

Needs a **second** model loaded in LM Studio, which the suite's own
``RUN_LMSTUDIO`` gate cannot express — so this scenario skips at runtime when
``LMSTUDIO_OVERRIDE_MODEL`` is unset, using the sanctioned
``set <VAR> to run <suite>`` phrasing the zero-skip session guard allows
(spec-0029 R9; the ``LMSTUDIO_`` prefix is proven sanctioned in
``tests/tooling/test_collection_gate.py``).

What the live run adds over tier-1 flow I6: the override client is a real
``LMStudioClient`` built by the real factory against a real second model, so a
bug in how ``build_agent_llm_overrides`` copies the base config — a dropped
base URL, a lost timeout, a wrong API key — surfaces as a failed request
rather than passing unnoticed against a fake.

Skipped unless ``RUN_LMSTUDIO=1`` **and** ``LMSTUDIO_OVERRIDE_MODEL`` is set.
"""

from __future__ import annotations

import logging
import os

import httpx
import pytest

from mangomas.api.app import create_app
from mangomas.composition import build_orchestrator
from mangomas.config import AgentSettings
from tests.constants import (
    ASGI_TEST_BASE_URL,
    LMSTUDIO_OVERRIDE_MODEL_ENV,
)
from tests.lmstudio.conftest import make_lmstudio_settings, orchestrator_cleanup

logger = logging.getLogger(__name__)

_BODY = {"messages": [{"role": "user", "content": "Say hello in one short sentence."}]}


@pytest.fixture
def lmstudio_override_model() -> str:
    """A second loaded model id, or a sanctioned runtime skip."""
    model = os.environ.get(LMSTUDIO_OVERRIDE_MODEL_ENV, "")
    if not model:
        pytest.skip(f"set {LMSTUDIO_OVERRIDE_MODEL_ENV} to run LM Studio override tests")
    return model


@pytest.mark.lmstudio
async def test_chat_answers_from_its_overridden_model(
    lmstudio_base_url: str,
    lmstudio_model: str,
    lmstudio_override_model: str,
    lmstudio_client_timeout: float,
) -> None:
    """The chat agent's override client is built, used, and closed.

    The oracle is structural: a real completion comes back, and the extras
    dict carries a client for ``chat`` that is *not* ``ctx.llm``. Comparing
    the two models' output text would be exactly the model-dependent
    assertion spec-0029 R2.3 forbids — two models answering the same prompt
    say different things by design, and one of them saying the "wrong" thing
    is not a defect in this repository.
    """
    settings = make_lmstudio_settings(
        lmstudio_base_url,
        lmstudio_model,
        agents={"chat": AgentSettings(model_override=lmstudio_override_model)},
    )
    orch = build_orchestrator(settings)
    app = create_app(orchestrator=orch)

    overrides = orch.context.extras.get("agent_llm_overrides", {})
    assert "chat" in overrides, "an override differing from the base model must build a client"
    assert overrides["chat"] is not orch.context.llm, (
        "the override must be a distinct client, not the shared one"
    )

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=ASGI_TEST_BASE_URL) as client:
            response = await client.post(
                "/agents/chat/invoke", json=_BODY, timeout=lmstudio_client_timeout
            )

        assert response.status_code == 200, response.text
        assert response.json()["content"], "the override client must return real content"
        logger.info(
            "Override client answered a live request",
            extra={"base_model": lmstudio_model, "override_model": lmstudio_override_model},
        )
    finally:
        # Exercises the real teardown path: `_AgentLLMOverrideCloseMixin` must
        # close the override's httpx pool alongside `ctx.llm`. Leaking one per
        # overridden agent is the failure this closes.
        await orch.aclose()


@pytest.mark.lmstudio
async def test_an_override_equal_to_the_base_model_builds_nothing(
    lmstudio_base_url: str,
    lmstudio_model: str,
) -> None:
    """The dedup direction: same model id ⇒ no second client, no second pool.

    Needs no second model, so it runs whenever the suite does — and it is the
    half that catches an override implementation that always allocated.
    """
    settings = make_lmstudio_settings(
        lmstudio_base_url,
        lmstudio_model,
        agents={"chat": AgentSettings(model_override=lmstudio_model)},
    )
    orch = build_orchestrator(settings)

    async with orchestrator_cleanup(orch):
        assert orch.context.extras.get("agent_llm_overrides") == {}
