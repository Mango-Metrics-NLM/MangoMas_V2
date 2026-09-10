"""RAG retrieval-tool wiring."""

from __future__ import annotations

from mangomas.composition import _vector_registry, build_orchestrator, embedding_registry
from mangomas.config import Settings
from tests.composition.helpers import close_repo, rag_enabled_settings
from tests.fakes import FakeEmbeddingClient, FakeVectorStore


def test_build_orchestrator_wires_retrieval_tool_when_both_enabled() -> None:
    settings = rag_enabled_settings()
    with (
        embedding_registry.scoped("lmstudio", lambda _cfg: FakeEmbeddingClient()),
        _vector_registry.scoped("chroma", lambda _cfg: FakeVectorStore()),
    ):
        orch = build_orchestrator(settings)
        try:
            tools = orch.context.tools
            assert tools is not None
            assert "retrieve" in tools.available()
        finally:
            close_repo(orch)


def test_build_orchestrator_no_tools_when_only_embeddings_enabled() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.embeddings.enabled = True
    settings.embeddings.provider = "lmstudio"
    with embedding_registry.scoped("lmstudio", lambda _cfg: FakeEmbeddingClient()):
        orch = build_orchestrator(settings)
        try:
            assert orch.context.tools is None
        finally:
            close_repo(orch)


def test_build_orchestrator_no_tools_when_rag_disabled() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert orch.context.tools is None
    finally:
        close_repo(orch)
