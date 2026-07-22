"""LM Studio embedding adapter live smoke test (spec 0013). Gated by RUN_LMSTUDIO=1.

Closes the coverage gap: ``LMStudioEmbeddingClient`` previously had only
respx-mocked unit tests. Requires a running LM Studio server with an embedding
model loaded (set ``LMSTUDIO_EMBEDDING_MODEL`` to its id).
"""

from __future__ import annotations

import math
import os

import pytest

from mangomas.adapters.embeddings.lmstudio import LMStudioEmbeddingClient
from mangomas.config import DEFAULT_EMBEDDINGS_BASE_URL, DEFAULT_EMBEDDINGS_MODEL
from tests.constants import LMSTUDIO_BASE_URL_ENV, LMSTUDIO_EMBEDDING_MODEL_ENV

pytestmark = pytest.mark.lmstudio


async def test_lmstudio_embed_batch_returns_finite_vectors() -> None:
    base_url = os.getenv(LMSTUDIO_BASE_URL_ENV, DEFAULT_EMBEDDINGS_BASE_URL)
    model = os.getenv(LMSTUDIO_EMBEDDING_MODEL_ENV, DEFAULT_EMBEDDINGS_MODEL)
    client = LMStudioEmbeddingClient(base_url=base_url, model=model)
    try:
        vectors = await client.embed_batch(["hello world", "goodbye world"])
        assert len(vectors) == 2
        assert len(vectors[0]) == len(vectors[1]) > 0
        assert all(math.isfinite(x) for x in vectors[0])
        # embed() is the single-text convenience over embed_batch().
        single = await client.embed("just one")
        assert len(single) == len(vectors[0])
    finally:
        await client.aclose()
