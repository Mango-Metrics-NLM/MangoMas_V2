"""Shared fixtures for the Vertex AI live E2E suite.

All tests in this directory are gated by ``RUN_VERTEX=1`` and the
``vertex`` path component. They require ADC / Workload Identity
Federation (``gcloud auth application-default login``) and a valid
``VERTEX_PROJECT`` / ``VERTEX_MODEL`` env var pair.

Fixture shape mirrors ``tests/lmstudio/conftest.py`` so the test patterns
are interchangeable.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI

from mangomas.adapters.storage import SQLiteRepository
from mangomas.api.app import create_app
from mangomas.composition import build_orchestrator
from mangomas.config import (
    DEFAULT_LLM_TEMPERATURE,
    DEFAULT_VERTEX_LOCATION,
    DEFAULT_VERTEX_MAX_OUTPUT_TOKENS,
    DEFAULT_VERTEX_MODEL,
    DBSettings,
    LLMSettings,
    Settings,
)
from mangomas.core import Orchestrator
from tests.constants import (
    VERTEX_LOCATION_ENV,
    VERTEX_MODEL_ENV,
    VERTEX_PROJECT_ENV,
)

# Network calls to Vertex can exceed the default LLM timeout on cold
# starts; bump for the suite. Developers can still override via
# MANGOMAS_LLM__TIMEOUT_SECONDS in their env.
VERTEX_E2E_TIMEOUT_SECONDS: float = 120.0

_E2E_DB_URL: str = "sqlite:///:memory:"


def make_vertex_settings(
    project: str,
    location: str,
    model: str,
    *,
    timeout_seconds: float = VERTEX_E2E_TIMEOUT_SECONDS,
) -> Settings:
    """Construct a fresh :class:`Settings` pointed at Vertex + in-memory SQLite."""
    return Settings(
        llm=LLMSettings(
            provider="vertex",
            project=project,
            location=location,
            model=model,
            timeout_seconds=timeout_seconds,
            temperature=DEFAULT_LLM_TEMPERATURE,
            max_output_tokens=DEFAULT_VERTEX_MAX_OUTPUT_TOKENS,
        ),
        db=DBSettings(provider="sqlite", url=_E2E_DB_URL),
    )


@asynccontextmanager
async def orchestrator_cleanup(orch: Orchestrator) -> AsyncIterator[None]:
    """Mirror of the LM Studio cleanup helper for symmetric E2E ergonomics."""
    try:
        yield
    finally:
        ctx = orch.context
        if hasattr(ctx.llm, "aclose"):
            await ctx.llm.aclose()
        if isinstance(ctx.repo, SQLiteRepository):
            ctx.repo.close()


@pytest.fixture
def vertex_project() -> str:
    project = os.environ.get(VERTEX_PROJECT_ENV)
    if not project:
        pytest.skip(f"set {VERTEX_PROJECT_ENV} to run Vertex E2E tests")
    return project


@pytest.fixture
def vertex_location() -> str:
    return os.environ.get(VERTEX_LOCATION_ENV, DEFAULT_VERTEX_LOCATION)


@pytest.fixture
def vertex_model() -> str:
    return os.environ.get(VERTEX_MODEL_ENV, DEFAULT_VERTEX_MODEL)


@pytest.fixture
async def vertex_orchestrator(
    vertex_project: str,
    vertex_location: str,
    vertex_model: str,
) -> AsyncIterator[Orchestrator]:
    """Live Vertex-backed orchestrator with an in-memory SQLite repo."""
    settings = make_vertex_settings(vertex_project, vertex_location, vertex_model)
    orch = build_orchestrator(settings)
    async with orchestrator_cleanup(orch):
        yield orch


@pytest.fixture
def vertex_app(vertex_orchestrator: Orchestrator) -> Iterator[FastAPI]:
    """ASGI app with the live Vertex orchestrator pre-injected (no lifespan)."""
    app = create_app(orchestrator=vertex_orchestrator)
    try:
        yield app
    finally:
        pass  # cleanup handled in vertex_orchestrator teardown
