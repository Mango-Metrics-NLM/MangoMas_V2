"""Vertex E2E — scenario 6: error path against a deliberately bogus model id.

Targets a model that does not exist in Vertex; the Vertex SDK should
raise a :class:`google.api_core.exceptions.GoogleAPIError` (typically
``NotFound`` / ``InvalidArgument``), which the adapter translates to
:class:`VertexError` (subclass of :class:`LLMBadResponse`), which maps to
HTTP 502 via ``_ERROR_STATUS``.
"""

from __future__ import annotations

import logging
import os

import httpx
import pytest

from mangomas.api.app import create_app
from mangomas.composition import build_orchestrator
from tests.constants import (
    ASGI_TEST_BASE_URL,
    HTTPX_ERROR_PATH_TIMEOUT_SECONDS,
    VERTEX_BAD_MODEL_ENV,
)
from tests.vertex.conftest import make_vertex_settings, orchestrator_cleanup

logger = logging.getLogger(__name__)
pytestmark = pytest.mark.vertex

_DEFAULT_BAD_MODEL: str = "does-not-exist-model"


async def test_unknown_model_returns_502(
    vertex_project: str,
    vertex_location: str,
) -> None:
    bad_model = os.environ.get(VERTEX_BAD_MODEL_ENV, _DEFAULT_BAD_MODEL)
    settings = make_vertex_settings(vertex_project, vertex_location, bad_model)

    orch = build_orchestrator(settings)
    app = create_app(orchestrator=orch)
    transport = httpx.ASGITransport(app=app)
    async with (
        orchestrator_cleanup(orch),
        httpx.AsyncClient(transport=transport, base_url=ASGI_TEST_BASE_URL) as client,
    ):
        response = await client.post(
            "/agents/chat/invoke",
            json={"messages": [{"role": "user", "content": "hi"}]},
            timeout=HTTPX_ERROR_PATH_TIMEOUT_SECONDS,
        )

    assert response.status_code == 502, response.text
    body = response.json()
    # Adapter maps Google API errors to VertexError(LLMBadResponse) → code llm_bad_response.
    assert body["error"] == "llm_bad_response"
