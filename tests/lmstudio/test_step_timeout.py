"""LM Studio E2E — scenario 8: the per-step timeout against a real model (spec-0026).

Why this is hardware-independent despite timing a real network call: the
budget is ``LIVE_STEP_TIMEOUT_SECONDS`` — a value below one network round
trip. No hardware, GPU or CPU, returns a completion that fast, so the timeout
fires identically everywhere. The test never asserts *how long* anything took
(spec-0029 R2.1); it asserts which typed error came back.

The negative direction uses the CPU-sized budget instead, so a machine slow
enough to need 200 seconds still passes it.

Skipped unless ``RUN_LMSTUDIO=1``.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from mangomas.api.app import create_app
from mangomas.composition import build_orchestrator
from mangomas.config import LoopSettings
from mangomas.errors import StepTimeout
from tests.constants import (
    ASGI_TEST_BASE_URL,
    HTTPX_ERROR_PATH_TIMEOUT_SECONDS,
    LIVE_STEP_TIMEOUT_SECONDS,
)
from tests.lmstudio.conftest import make_lmstudio_settings, orchestrator_cleanup

logger = logging.getLogger(__name__)

_BODY = {"messages": [{"role": "user", "content": "Say hello."}]}
_STEP_TIMEOUT_STATUS = 504


@pytest.mark.lmstudio
async def test_sub_round_trip_budget_returns_the_504_envelope(
    lmstudio_base_url: str,
    lmstudio_model: str,
) -> None:
    """A budget below one round trip always expires, on any hardware.

    The client budget here is the *error-path* one: this request is expected
    to fail fast server-side, so waiting the full CPU-sized budget for it
    would only slow the suite down.
    """
    settings = make_lmstudio_settings(
        lmstudio_base_url,
        lmstudio_model,
        loop=LoopSettings(step_timeout_seconds=LIVE_STEP_TIMEOUT_SECONDS),
    )
    orch = build_orchestrator(settings)
    app = create_app(orchestrator=orch)

    async with orchestrator_cleanup(orch):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=ASGI_TEST_BASE_URL) as client:
            response = await client.post(
                "/agents/chat/invoke",
                json=_BODY,
                timeout=HTTPX_ERROR_PATH_TIMEOUT_SECONDS,
            )

        assert response.status_code == _STEP_TIMEOUT_STATUS, response.text
        assert response.json()["error"] == StepTimeout(LIVE_STEP_TIMEOUT_SECONDS).code
        logger.info("Live step timeout produced the expected envelope")

        # The step was cancelled before completing, so no half turn was stored.
        repo = orch.context.repo
        assert repo is not None
        assert await repo.list_turns(limit=5) == []


@pytest.mark.lmstudio
async def test_the_cpu_sized_budget_lets_a_real_completion_through(
    lmstudio_base_url: str,
    lmstudio_model: str,
    lmstudio_client_timeout: float,
) -> None:
    """The other direction: the default budget must not cut off a real model.

    This is the assertion that would fail on a slow CPU box if the budgets
    were mis-sized — which is exactly the class of defect spec-0029 exists to
    prevent, so it is worth a live scenario rather than only a fake-backed one.
    """
    settings = make_lmstudio_settings(lmstudio_base_url, lmstudio_model)
    orch = build_orchestrator(settings)
    app = create_app(orchestrator=orch)

    async with orchestrator_cleanup(orch):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=ASGI_TEST_BASE_URL) as client:
            response = await client.post(
                "/agents/chat/invoke", json=_BODY, timeout=lmstudio_client_timeout
            )

        assert response.status_code == 200, response.text
        assert response.json()["content"], "a real completion must come back non-empty"
