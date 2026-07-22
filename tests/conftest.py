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
from mangomas.secrets import secrets_registry
from tests.fakes import FakeLLM, FakeMemoryRepository, FakeRepository, FakeTool

# ── Cross-test isolation for lazy-registered cloud providers ──────────────────


@pytest.fixture(autouse=True)
def _teardown_lazy_gcp_secrets() -> Iterator[None]:
    """Pop any GCP secrets provider lazy-registered by build_orchestrator.

    The provider is registered inside ``build_orchestrator`` when
    ``secrets.provider == "gcp"`` and lives on the module-level
    ``secrets_registry`` instance. Without this teardown, a test that
    exercises the gcp path would leak a (project-id-bound) provider
    into the next test's ``secrets_registry.available()`` view.
    """
    yield
    secrets_registry._store.pop("gcp", None)


# ── Collection gates ────────────────────────────────────────────────────────


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip integration / cloud-provider tests unless explicitly enabled."""
    run_integration = os.getenv("RUN_INTEGRATION") == "1"
    run_lmstudio = os.getenv("RUN_LMSTUDIO") == "1"
    run_postgres = os.getenv("RUN_POSTGRES") == "1"
    run_vertex = os.getenv("RUN_VERTEX") == "1"
    run_gcp_secrets = os.getenv("RUN_GCP_SECRETS") == "1"
    run_gcp_trace = os.getenv("RUN_GCP_TRACE") == "1"
    run_embeddings_local = os.getenv("RUN_EMBEDDINGS_LOCAL") == "1"
    run_rag = os.getenv("RUN_RAG") == "1"
    run_langfuse = os.getenv("RUN_LANGFUSE") == "1"
    integration_skip = pytest.mark.skip(reason="set RUN_INTEGRATION=1 to run integration tests")
    lmstudio_skip = pytest.mark.skip(reason="set RUN_LMSTUDIO=1 to run LM Studio tests")
    postgres_skip = pytest.mark.skip(reason="set RUN_POSTGRES=1 to run Postgres tests")
    vertex_skip = pytest.mark.skip(reason="set RUN_VERTEX=1 to run Vertex AI tests")
    gcp_secrets_skip = pytest.mark.skip(
        reason="set RUN_GCP_SECRETS=1 to run GCP Secret Manager tests"
    )
    gcp_trace_skip = pytest.mark.skip(
        reason="set RUN_GCP_TRACE=1 to run Cloud Trace exporter tests"
    )
    embeddings_local_skip = pytest.mark.skip(
        reason="set RUN_EMBEDDINGS_LOCAL=1 to run sentence-transformers tests"
    )
    rag_skip = pytest.mark.skip(reason="set RUN_RAG=1 to run chromadb-backed RAG tests")
    langfuse_skip = pytest.mark.skip(reason="set RUN_LANGFUSE=1 to run Langfuse sink tests")

    for item in items:
        path_parts = set(Path(str(item.fspath)).parts)
        if "integration" in path_parts and not run_integration:
            item.add_marker(integration_skip)
        if "lmstudio" in item.keywords and not run_lmstudio:
            item.add_marker(lmstudio_skip)
        if ("postgres" in path_parts or "postgres" in item.keywords) and not run_postgres:
            item.add_marker(postgres_skip)
        if "vertex" in item.keywords and not run_vertex:
            item.add_marker(vertex_skip)
        if "gcp_secrets" in item.keywords and not run_gcp_secrets:
            item.add_marker(gcp_secrets_skip)
        if "gcp_trace" in item.keywords and not run_gcp_trace:
            item.add_marker(gcp_trace_skip)
        if "embeddings_local" in item.keywords and not run_embeddings_local:
            item.add_marker(embeddings_local_skip)
        # Gate on the explicit ``@pytest.mark.rag`` marker only — the
        # ``tests/rag/`` directory name would otherwise leak into ``keywords``
        # and wrongly skip the pure-domain chunker/models unit tests.
        if item.get_closest_marker("rag") is not None and not run_rag:
            item.add_marker(rag_skip)
        if "langfuse" in item.keywords and not run_langfuse:
            item.add_marker(langfuse_skip)


# ── Settings fixture ──────────────────────────────────────────────────────────


@pytest.fixture
def settings() -> Iterator[Settings]:
    """Return a fresh Settings instance and clear the lru_cache on teardown."""
    get_settings.cache_clear()
    yield Settings()
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Read env-driven settings fresh per test so a cached value never leaks.

    Shared by every env-toggling API test (CORS / auth / backpressure /
    workflow-endpoint), which previously each redefined this fixture.
    """
    get_settings.cache_clear()
    yield
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
