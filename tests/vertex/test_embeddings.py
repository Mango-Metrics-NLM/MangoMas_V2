"""Vertex embedding adapter live smoke test (spec 0013). Gated by RUN_VERTEX=1.

Closes the coverage gap: ``VertexEmbeddingClient`` previously had only
fake-injected unit tests. Requires ADC auth + a ``VERTEX_PROJECT_ID``.
"""

from __future__ import annotations

import math
import os

import pytest

from mangomas.adapters.embeddings.vertex import VertexEmbeddingClient
from mangomas.config import DEFAULT_VERTEX_LOCATION
from tests.constants import (
    DEFAULT_VERTEX_EMBEDDING_MODEL,
    VERTEX_EMBEDDING_MODEL_ENV,
    VERTEX_LOCATION_ENV,
    VERTEX_PROJECT_ENV,
)

pytestmark = pytest.mark.vertex


async def test_vertex_embed_batch_returns_finite_vectors() -> None:
    project = os.getenv(VERTEX_PROJECT_ENV)
    if not project:
        pytest.skip(f"{VERTEX_PROJECT_ENV} not set")
    location = os.getenv(VERTEX_LOCATION_ENV, DEFAULT_VERTEX_LOCATION)
    model = os.getenv(VERTEX_EMBEDDING_MODEL_ENV, DEFAULT_VERTEX_EMBEDDING_MODEL)
    client = VertexEmbeddingClient(project_id=project, location=location, model=model)
    try:
        vectors = await client.embed_batch(["hello world", "goodbye world"])
        assert len(vectors) == 2
        assert len(vectors[0]) == len(vectors[1]) > 0
        assert all(math.isfinite(x) for x in vectors[0])
    finally:
        await client.aclose()
