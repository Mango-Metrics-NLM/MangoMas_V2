"""Vertex AI smoke test — connectivity check via ``client.ping()``.

Requires a reachable Vertex AI project; skipped unless ``RUN_VERTEX=1``.
"""

from __future__ import annotations

import logging

import pytest

from mangomas.core import Orchestrator

logger = logging.getLogger(__name__)


@pytest.mark.vertex
async def test_vertex_ping(vertex_orchestrator: Orchestrator) -> None:
    """A single minimal completion should succeed against the live project."""
    ctx = vertex_orchestrator.context
    ping = getattr(ctx.llm, "ping", None)
    assert ping is not None, "VertexClient must expose ping()"
    await ping()
