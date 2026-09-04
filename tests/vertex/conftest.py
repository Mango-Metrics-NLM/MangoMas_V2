"""Shared fixtures and helpers for the Vertex AI E2E suite.

Mirrors :mod:`tests.lmstudio.conftest` for the Vertex provider. All fixtures
read configuration from the environment with the canonical defaults from
:mod:`mangomas.config` as fallback. No project/location/model is hardcoded —
every value can be overridden via env vars.

Fixtures
--------
``vertex_project``
    Resolved Vertex project id (``VERTEX_PROJECT_ID``).
``vertex_location``
    Resolved Vertex location (``VERTEX_LOCATION``; defaults to
    ``mangomas.config.DEFAULT_VERTEX_LOCATION``).
``vertex_model``
    Resolved Vertex model id (``VERTEX_MODEL``; defaults to
    ``DEFAULT_VERTEX_TEST_MODEL``).
``vertex_orchestrator``
    A fully-wired :class:`~mangomas.core.Orchestrator` pointed at the live
    Vertex project. Uses an in-memory SQLite repo. Cleans up the LLM client
    on teardown.
``vertex_app``
    ASGI app built via :func:`~mangomas.api.app.create_app` with the
    orchestrator above injected.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi import FastAPI

from mangomas.api.app import create_app
from mangomas.composition import build_orchestrator
from mangomas.config import (
    DEFAULT_VERTEX_LOCATION,
    DBSettings,
    LLMSettings,
    Settings,
)
from mangomas.core import Orchestrator
from tests.constants import (
    DEFAULT_VERTEX_TEST_MODEL,
    IN_MEMORY_SQLITE_URL,
    VERTEX_CREDENTIALS_PATH_ENV,
    VERTEX_E2E_TIMEOUT_ENV,
    VERTEX_LOCATION_ENV,
    VERTEX_MODEL_ENV,
    VERTEX_PROJECT_ENV,
    client_timeout_for,
    resolve_live_timeout,
)
from tests.lmstudio.conftest import orchestrator_cleanup

_E2E_DB_URL: str = IN_MEMORY_SQLITE_URL


def make_vertex_settings(
    project_id: str,
    location: str,
    model: str,
    *,
    credentials_path: str | None = None,
    timeout_seconds: float | None = None,
) -> Settings:
    """Construct fresh :class:`Settings` pointed at Vertex + in-memory SQLite.

    *timeout_seconds* defaults to the env-resolved adapter budget
    (``VERTEX_E2E_TIMEOUT_SECONDS``), the same seam the LM Studio suite uses —
    Vertex round-trips the public Google API, so a cold first completion is
    just as hardware- and network-sensitive as a local model.
    """
    resolved = (
        resolve_live_timeout(VERTEX_E2E_TIMEOUT_ENV) if timeout_seconds is None else timeout_seconds
    )
    return Settings(
        llm=LLMSettings(
            provider="vertex",
            model=model,
            project_id=project_id,
            location=location,
            credentials_path=credentials_path,
            timeout_seconds=resolved,
        ),
        db=DBSettings(provider="sqlite", url=_E2E_DB_URL),
    )


@pytest.fixture
def vertex_timeout() -> float:
    """Adapter-side budget for a live Vertex call, from the env or the default."""
    return resolve_live_timeout(VERTEX_E2E_TIMEOUT_ENV)


@pytest.fixture
def vertex_client_timeout(vertex_timeout: float) -> float:
    """httpx client budget, derived from the adapter budget (never below it).

    Mirrors ``lmstudio_client_timeout``; this suite carried the identical
    60 s-client-around-a-240 s-adapter inversion (spec-0029 R2.1).
    """
    return client_timeout_for(vertex_timeout)


@pytest.fixture
def vertex_project() -> str:
    project = os.environ.get(VERTEX_PROJECT_ENV, "")
    if not project:
        pytest.skip(f"set {VERTEX_PROJECT_ENV} to run Vertex AI tests")
    return project


@pytest.fixture
def vertex_location() -> str:
    return os.environ.get(VERTEX_LOCATION_ENV, DEFAULT_VERTEX_LOCATION)


@pytest.fixture
def vertex_model() -> str:
    return os.environ.get(VERTEX_MODEL_ENV, DEFAULT_VERTEX_TEST_MODEL)


@pytest.fixture
def vertex_credentials_path() -> str | None:
    return os.environ.get(VERTEX_CREDENTIALS_PATH_ENV) or None


@pytest.fixture
async def vertex_orchestrator(
    vertex_project: str,
    vertex_location: str,
    vertex_model: str,
    vertex_credentials_path: str | None,
) -> AsyncIterator[Orchestrator]:
    """Orchestrator wired against the live Vertex project (in-memory SQLite)."""
    settings = make_vertex_settings(
        vertex_project,
        vertex_location,
        vertex_model,
        credentials_path=vertex_credentials_path,
    )
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
        # Cleanup happens in vertex_orchestrator teardown.
        pass
