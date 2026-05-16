"""LM Studio E2E — scenario 6: error path with a bad LM Studio base URL.

The original spec described "unknown model id" as the failure trigger, but
LM Studio silently serves from the currently-loaded model when given an
unknown id — it does NOT 4xx. To exercise the real ``LMStudioError`` →
``LLMBadResponse`` → ``502`` envelope path end-to-end, this scenario
instead points the LM Studio client at an API version prefix LM Studio
does not serve (``/v999``). LM Studio returns ``404 Not Found`` for the
unknown path, which ``_translate_httpx_error`` wraps into
``LMStudioError`` (a ``LLMBadResponse`` subclass) — and ``_ERROR_STATUS``
maps that to ``502 Bad Gateway``.

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
    DBSettings,
    LLMSettings,
    Settings,
)
from tests.lmstudio.conftest import LMSTUDIO_E2E_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

_BAD_API_VERSION_SUFFIX = "/v999"


@pytest.mark.lmstudio
async def test_bad_api_path_returns_502_envelope(
    lmstudio_base_url: str,
    lmstudio_model: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Strip the /v1 suffix (if any) and append a path LM Studio doesn't serve.
    base_without_version = lmstudio_base_url.rsplit("/v", 1)[0]
    bad_base_url = f"{base_without_version}{_BAD_API_VERSION_SUFFIX}"

    settings = Settings(
        llm=LLMSettings(
            provider="lmstudio",
            base_url=bad_base_url,
            model=lmstudio_model,
            api_key=DEFAULT_LLM_API_KEY,
            timeout_seconds=LMSTUDIO_E2E_TIMEOUT_SECONDS,
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
