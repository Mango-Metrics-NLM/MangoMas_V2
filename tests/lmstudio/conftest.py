"""Shared fixtures and helpers for the LM Studio E2E suite.

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

Helpers (importable)
--------------------
``parse_sse_data(line)``
    Decode a ``data: {...}`` SSE line into a dict (returns ``None`` for
    non-data frames).
``make_lmstudio_settings(base_url, model, *, timeout_seconds=...)``
    Build a fresh :class:`~mangomas.config.Settings` instance pointed at
    LM Studio with an in-memory SQLite repo — for scenarios that need to
    construct an orchestrator outside the standard fixture (e.g. to swap
    the LLM provider via :meth:`Registry.scoped`).
``orchestrator_cleanup(orch)``
    Async context manager that yields and then closes the LLM client and
    SQLite repo on the way out. Replaces the manual try/finally
    ``aclose() + close()`` pattern.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

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
    DBSettings,
    LLMSettings,
    Settings,
)
from mangomas.core import Orchestrator
from tests.constants import LMSTUDIO_BASE_URL_ENV, LMSTUDIO_MODEL_ENV

# Local-LLM runs on CPU can take well over 60s per completion. The fixtures
# bump the per-request timeout so the suite is patient enough for slow
# hardware; developers on faster setups can still override via
# MANGOMAS_LLM__TIMEOUT_SECONDS at the env level.
LMSTUDIO_E2E_TIMEOUT_SECONDS: float = 240.0

# In-memory SQLite URL used by every E2E scenario so a developer's local
# DB is never touched by an LM Studio test run.
_E2E_DB_URL: str = "sqlite:///:memory:"


# ── Shared utility helpers ───────────────────────────────────────────────────


def parse_sse_data(line: str) -> dict[str, Any] | None:
    """Decode a ``data: {...}`` SSE line. Returns ``None`` for non-data lines."""
    if not line.startswith("data: "):
        return None
    payload = line[len("data: ") :].strip()
    if not payload:
        return None
    decoded: dict[str, Any] = json.loads(payload)
    return decoded


def make_lmstudio_settings(
    base_url: str,
    model: str,
    *,
    timeout_seconds: float = LMSTUDIO_E2E_TIMEOUT_SECONDS,
) -> Settings:
    """Construct a fresh :class:`Settings` pointed at LM Studio + in-memory SQLite.

    Used by scenarios that build their own orchestrator (e.g. to swap the
    LLM provider via :meth:`Registry.scoped` or to target a bad URL path).
    """
    return Settings(
        llm=LLMSettings(
            provider="lmstudio",
            base_url=base_url,
            model=model,
            api_key=DEFAULT_LLM_API_KEY,
            timeout_seconds=timeout_seconds,
            temperature=DEFAULT_LLM_TEMPERATURE,
        ),
        db=DBSettings(provider="sqlite", url=_E2E_DB_URL),
    )


@asynccontextmanager
async def orchestrator_cleanup(orch: Orchestrator) -> AsyncIterator[None]:
    """Async context manager that closes the orchestrator's LLM + repo on exit.

    Replaces the boilerplate ``try: ... finally: ctx.llm.aclose() +
    ctx.repo.close()`` pattern across E2E scenarios that build their own
    orchestrator.
    """
    try:
        yield
    finally:
        ctx = orch.context
        if hasattr(ctx.llm, "aclose"):
            await ctx.llm.aclose()
        if isinstance(ctx.repo, SQLiteRepository):
            ctx.repo.close()


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
    settings = make_lmstudio_settings(lmstudio_base_url, lmstudio_model)
    orch = build_orchestrator(settings)
    async with orchestrator_cleanup(orch):
        yield orch


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
