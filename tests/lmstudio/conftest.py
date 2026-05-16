"""Shared fixtures for the LM Studio E2E suite.

All fixtures read configuration from the environment with the canonical
defaults from :mod:`mangomas.config` as fallback. No model ids or URLs are
hardcoded — every value can be overridden via env vars.

Fixtures
--------
``lmstudio_base_url``
    Resolved LM Studio base URL.
``lmstudio_model``
    Resolved LM Studio model id.
``lmstudio_orchestrator``
    A fully-wired :class:`~mangomas.core.Orchestrator` pointed at the live
    LM Studio instance. Uses an in-memory SQLite repo so tests don't touch
    the developer's local DB. Cleans up the LLM client on teardown.
``lmstudio_app``
    ASGI app built via :func:`~mangomas.api.app.create_app` with the
    orchestrator above injected (so no lifespan is run). Suitable for
    :class:`httpx.AsyncClient` over :class:`httpx.ASGITransport`.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi import FastAPI

from mangomas.adapters.storage import SQLiteRepository
from mangomas.api.app import create_app
from mangomas.composition import build_orchestrator
from mangomas.config import (
    DEFAULT_LLM_API_KEY,
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_TEMPERATURE,
    DEFAULT_LLM_TIMEOUT_SECONDS,
    DBSettings,
    LLMSettings,
    Settings,
)
from mangomas.core import Orchestrator
from tests.constants import LMSTUDIO_BASE_URL_ENV, LMSTUDIO_MODEL_ENV


@pytest.fixture
def lmstudio_base_url() -> str:
    """LM Studio base URL from ``LMSTUDIO_BASE_URL`` env var or default."""
    return os.environ.get(LMSTUDIO_BASE_URL_ENV, DEFAULT_LLM_BASE_URL)


@pytest.fixture
def lmstudio_model() -> str:
    """LM Studio model id from ``LMSTUDIO_MODEL`` env var or default."""
    return os.environ.get(LMSTUDIO_MODEL_ENV, DEFAULT_LLM_MODEL)


@pytest.fixture
async def lmstudio_orchestrator(
    lmstudio_base_url: str,
    lmstudio_model: str,
) -> AsyncIterator[Orchestrator]:
    """Orchestrator wired against the live LM Studio instance.

    The settings are built explicitly (not via the cached ``get_settings``)
    so that the developer's local env vars cannot accidentally redirect
    persistence to a real database. Storage is always in-memory SQLite.
    """
    settings = Settings(
        llm=LLMSettings(
            provider="lmstudio",
            base_url=lmstudio_base_url,
            model=lmstudio_model,
            api_key=DEFAULT_LLM_API_KEY,
            timeout_seconds=DEFAULT_LLM_TIMEOUT_SECONDS,
            temperature=DEFAULT_LLM_TEMPERATURE,
        ),
        db=DBSettings(provider="sqlite", url="sqlite:///:memory:"),
    )
    orch = build_orchestrator(settings)
    try:
        yield orch
    finally:
        ctx = orch.context
        if hasattr(ctx.llm, "aclose"):
            await ctx.llm.aclose()
        if ctx.repo is not None:
            ctx.repo.close()


@pytest.fixture
def lmstudio_app(lmstudio_orchestrator: Orchestrator) -> Iterator[FastAPI]:
    """ASGI app with the live orchestrator pre-injected (no lifespan)."""
    app = create_app(orchestrator=lmstudio_orchestrator)
    try:
        yield app
    finally:
        # ``create_app(orchestrator=...)`` skips the lifespan, so cleanup
        # of the LLM/repo happens in :func:`lmstudio_orchestrator` teardown.
        pass


# Silence unused-import warning for SQLiteRepository — kept available for
# downstream tests that may want to construct an alternative orchestrator.
__all__ = ["SQLiteRepository"]
