"""LM Studio E2E — scenario 6: error path with an unavailable model id.

Points the orchestrator at an unknown model and asserts that the invoke
endpoint returns the typed 502 envelope from ``LMStudioError`` /
``LLMBadResponse`` (mapped by :data:`~mangomas.api.app._ERROR_STATUS`).

Skipped unless ``RUN_LMSTUDIO=1``.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from mangomas.adapters.storage import SQLiteRepository
from mangomas.api.app import create_app
from mangomas.composition import build_orchestrator
from mangomas.config import (
    DEFAULT_LLM_API_KEY,
    DEFAULT_LLM_TEMPERATURE,
    DEFAULT_LLM_TIMEOUT_SECONDS,
    DBSettings,
    LLMSettings,
    Settings,
)

logger = logging.getLogger(__name__)

_UNKNOWN_MODEL_ID = "no-such-model-deliberately-bogus-id"


@pytest.mark.lmstudio
async def test_unknown_model_returns_502_envelope(
    lmstudio_base_url: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(
        llm=LLMSettings(
            provider="lmstudio",
            base_url=lmstudio_base_url,
            model=_UNKNOWN_MODEL_ID,
            api_key=DEFAULT_LLM_API_KEY,
            timeout_seconds=DEFAULT_LLM_TIMEOUT_SECONDS,
            temperature=DEFAULT_LLM_TEMPERATURE,
        ),
        db=DBSettings(provider="sqlite", url="sqlite:///:memory:"),
    )
    orch = build_orchestrator(settings)
    app = create_app(orchestrator=orch)
    transport = httpx.ASGITransport(app=app)
    try:
        with caplog.at_level(logging.WARNING, logger="mangomas.api.app"):
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                response = await client.post(
                    "/agents/chat/invoke",
                    json={"messages": [{"role": "user", "content": "hello"}]},
                    timeout=30.0,
                )
    finally:
        ctx = orch.context
        await ctx.llm.aclose()
        if isinstance(ctx.repo, SQLiteRepository):
            ctx.repo.close()

    assert response.status_code == 502, response.text
    body = response.json()
    assert body["error"] == "llm_bad_response"
    assert "message" in body
    # A structured warning must be logged for the failure.
    error_messages = [rec.message for rec in caplog.records if "Request error" in rec.message]
    assert any(
        "llm_bad_response" in msg for msg in error_messages
    ), "expected a structured 'Request error llm_bad_response' warning"
