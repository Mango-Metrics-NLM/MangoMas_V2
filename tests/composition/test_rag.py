"""RAG retrieval-tool wiring."""

from __future__ import annotations

from mangomas.composition import _vector_registry, build_orchestrator, embedding_registry
from mangomas.config import Settings
from tests.composition.helpers import close_repo, rag_enabled_settings
from tests.constants import IN_MEMORY_SQLITE_URL
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
    settings.db.url = IN_MEMORY_SQLITE_URL
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
    settings.db.url = IN_MEMORY_SQLITE_URL
    orch = build_orchestrator(settings)
    try:
        assert orch.context.tools is None
    finally:
        close_repo(orch)


async def test_build_orchestrator_wires_max_top_k_from_settings() -> None:
    """The ceiling reaches the tool through ``_build_rag_tools``. Mutation
    (proved while writing): dropping the ``max_top_k=`` pass-through in
    ``builder.py`` leaves the default ceiling and the tool returns 3 passages."""
    settings = rag_enabled_settings()
    settings.vector.top_k = 1
    settings.vector.max_top_k = 2
    emb, store = FakeEmbeddingClient(), FakeVectorStore()
    for i in range(3):
        text = f"doc {i}"
        await store.upsert(
            ids=[f"{i}#0"],
            embeddings=[await emb.embed(text)],
            documents=[text],
            metadatas=[{"source": f"{i}.txt", "index": 0}],
        )
    with (
        embedding_registry.scoped("lmstudio", lambda _cfg: emb),
        _vector_registry.scoped("chroma", lambda _cfg: store),
    ):
        orch = build_orchestrator(settings)
        try:
            tools = orch.context.tools
            assert tools is not None
            out = await tools.get("retrieve").execute({"query": "doc 1", "top_k": 10**6})
        finally:
            close_repo(orch)
    assert out.count("score=") == 2
