"""Shared pytest fixtures."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from mangomas.adapters.storage import SQLiteRepository
from mangomas.agents import ChatAgent
from mangomas.config import Settings, get_settings
from mangomas.core import AgentContext, Orchestrator

# Re-export fakes so existing ``from tests.conftest import FakeLLM`` imports
# continue to work during the one-cycle migration window.
from tests.fakes import (
    FakeLLM,
    FakeMemoryRepository,
    FakeRepository,
    FakeTool,
    NonPingableFakeLLM,
)

__all__ = ["FakeLLM", "FakeMemoryRepository", "FakeRepository", "FakeTool", "NonPingableFakeLLM"]


# ── Collection gates ────────────────────────────────────────────────────────


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip integration / cloud-provider tests unless explicitly enabled."""
    run_integration = os.getenv("RUN_INTEGRATION") == "1"
    run_lmstudio = os.getenv("RUN_LMSTUDIO") == "1"
    run_postgres = os.getenv("RUN_POSTGRES") == "1"
    run_vertex = os.getenv("RUN_VERTEX") == "1"
    run_gcp_secrets = os.getenv("RUN_GCP_SECRETS") == "1"
    integration_skip = pytest.mark.skip(reason="set RUN_INTEGRATION=1 to run integration tests")
    lmstudio_skip = pytest.mark.skip(reason="set RUN_LMSTUDIO=1 to run LM Studio tests")
    postgres_skip = pytest.mark.skip(reason="set RUN_POSTGRES=1 to run Postgres tests")
    vertex_skip = pytest.mark.skip(reason="set RUN_VERTEX=1 to run Vertex AI tests")
    gcp_secrets_skip = pytest.mark.skip(
        reason="set RUN_GCP_SECRETS=1 to run GCP Secret Manager tests"
    )

    for item in items:
        path_parts = set(Path(str(item.fspath)).parts)
        if "integration" in path_parts and not run_integration:
            item.add_marker(integration_skip)
        if "lmstudio" in item.keywords and not run_lmstudio:
            item.add_marker(lmstudio_skip)
        if ("postgres" in path_parts or "postgres" in item.keywords) and not run_postgres:
            item.add_marker(postgres_skip)
        if ("vertex" in path_parts or "vertex" in item.keywords) and not run_vertex:
            item.add_marker(vertex_skip)
        if "gcp_secrets" in item.keywords and not run_gcp_secrets:
            item.add_marker(gcp_secrets_skip)


# ── Settings fixture ──────────────────────────────────────────────────────────


@pytest.fixture
def settings() -> Iterator[Settings]:
    """Return a fresh Settings instance and clear the lru_cache on teardown."""
    get_settings.cache_clear()
    yield Settings()
    get_settings.cache_clear()


# ── Storage fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def repo() -> Iterator[SQLiteRepository]:
    """In-memory SQLiteRepository (for storage-layer tests)."""
    r = SQLiteRepository(":memory:")
    yield r
    r.close()


@pytest.fixture
def fake_repo() -> FakeRepository:
    """Pure in-memory FakeRepository (for unit tests that don't touch SQLite)."""
    return FakeRepository()


@pytest.fixture
def fake_memory() -> FakeMemoryRepository:
    """Pure in-memory FakeMemoryRepository (for unit tests)."""
    return FakeMemoryRepository()


# ── LLM fixture ───────────────────────────────────────────────────────────────


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def fake_tool() -> FakeTool:
    return FakeTool()


# ── Orchestrator fixture ──────────────────────────────────────────────────────


@pytest.fixture
def orchestrator(fake_llm: FakeLLM, repo: SQLiteRepository) -> Orchestrator:
    ctx = AgentContext(llm=fake_llm, repo=repo)
    orch = Orchestrator(ctx)
    orch.register(ChatAgent())
    return orch
