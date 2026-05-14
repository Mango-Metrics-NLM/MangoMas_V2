"""LM Studio integration smoke tests.

Skipped by default.  Set ``RUN_LMSTUDIO=1`` to run these tests against a live
LM Studio server.

Environment variables consumed by this module
---------------------------------------------
``LMSTUDIO_BASE_URL``
    Base URL for the LM Studio OpenAI-compatible server.
    Defaults to :data:`~mangomas.config.DEFAULT_LLM_BASE_URL`
    (``http://localhost:1234/v1``).
``LMSTUDIO_MODEL``
    Model identifier to use in completion tests.
    Defaults to :data:`~mangomas.config.DEFAULT_LLM_MODEL` (``local-model``).
    Example: ``google/gemma-4-e4b``.

All test parameters are read dynamically at test-execution time so that a
single environment override affects all tests in this module without requiring
code changes.  No model ids are hardcoded.
"""

from __future__ import annotations

import logging
import os

import pytest

from mangomas.adapters.llm.lmstudio import LMStudioClient
from mangomas.config import DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL

logger = logging.getLogger(__name__)

# ── Environment-variable names (single source of truth) ──────────────────────

_ENV_BASE_URL: str = "LMSTUDIO_BASE_URL"
_ENV_MODEL: str = "LMSTUDIO_MODEL"


def _base_url() -> str:
    """Return the LM Studio base URL from env or the configured default."""
    return os.environ.get(_ENV_BASE_URL, DEFAULT_LLM_BASE_URL)


def _model() -> str:
    """Return the LM Studio model id from env or the configured default."""
    return os.environ.get(_ENV_MODEL, DEFAULT_LLM_MODEL)


# ── Smoke test ────────────────────────────────────────────────────────────────


@pytest.mark.lmstudio
async def test_lmstudio_ping() -> None:
    """Verify the LM Studio server is reachable via ``GET /models``.

    Exercises :meth:`~mangomas.adapters.llm.lmstudio.LMStudioClient.ping`.
    Asserts that the call completes without raising an error, which means the
    server responded with a 2xx status code for the ``/models`` endpoint.

    Skipped unless ``RUN_LMSTUDIO=1`` is set.
    """
    base_url = _base_url()
    model = _model()
    logger.info(
        "LM Studio ping test starting",
        extra={"base_url": base_url, "model": model},
    )
    client = LMStudioClient(base_url=base_url, model=model)
    try:
        await client.ping()
        logger.info("LM Studio ping succeeded", extra={"base_url": base_url})
    finally:
        await client.aclose()
