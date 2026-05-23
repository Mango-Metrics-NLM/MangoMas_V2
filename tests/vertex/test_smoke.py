"""Vertex E2E — scenario 1: readiness ping."""

from __future__ import annotations

import logging

import pytest

from mangomas.adapters.llm.vertex import VertexLLMClient
from mangomas.config import (
    DEFAULT_LLM_TEMPERATURE,
    DEFAULT_VERTEX_MAX_OUTPUT_TOKENS,
)

logger = logging.getLogger(__name__)
pytestmark = pytest.mark.vertex


async def test_vertex_ping_against_live_endpoint(
    vertex_project: str,
    vertex_location: str,
    vertex_model: str,
) -> None:
    client = VertexLLMClient(
        project=vertex_project,
        location=vertex_location,
        model=vertex_model,
        request_timeout_seconds=60.0,
        default_temperature=DEFAULT_LLM_TEMPERATURE,
        max_output_tokens=DEFAULT_VERTEX_MAX_OUTPUT_TOKENS,
    )
    try:
        await client.ping()
    finally:
        await client.aclose()
