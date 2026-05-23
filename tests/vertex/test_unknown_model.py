"""Vertex AI E2E — error path with an unknown model id.

Unlike LM Studio (which silently serves from the loaded model), Vertex AI
returns a ``404 NotFound`` for an unrecognised model. The adapter wraps it
into :class:`VertexError` (a :class:`LLMBadResponse` subclass), which the
API surface maps to ``502 Bad Gateway`` via ``_ERROR_STATUS``.

Skipped unless ``RUN_VERTEX=1``.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from mangomas.api.app import create_app
from mangomas.composition import build_orchestrator
from tests.constants import ASGI_TEST_BASE_URL, HTTPX_ERROR_PATH_TIMEOUT_SECONDS
from tests.lmstudio.conftest import orchestrator_cleanup
from tests.vertex.conftest import make_vertex_settings

logger = logging.getLogger(__name__)

_BOGUS_MODEL = "this-model-definitely-does-not-exist-9999"


@pytest.mark.vertex
async def test_unknown_model_returns_502_envelope(
    vertex_project: str,
    vertex_location: str,
    vertex_credentials_path: str | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = make_vertex_settings(
        vertex_project,
        vertex_location,
        _BOGUS_MODEL,
        credentials_path=vertex_credentials_path,
    )
    orch = build_orchestrator(settings)
    app = create_app(orchestrator=orch)
    transport = httpx.ASGITransport(app=app)
    async with orchestrator_cleanup(orch):
        with caplog.at_level(logging.WARNING, logger="mangomas.api.app"):
            async with httpx.AsyncClient(
                transport=transport, base_url=ASGI_TEST_BASE_URL
            ) as client:
                response = await client.post(
                    "/agents/chat/invoke",
                    json={"messages": [{"role": "user", "content": "hello"}]},
                    timeout=HTTPX_ERROR_PATH_TIMEOUT_SECONDS,
                )

    assert response.status_code == 502, response.text
    body = response.json()
    assert body["error"] == "llm_bad_response"
    assert "message" in body
