"""Tests for the composition root."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import mangomas.composition as composition_module
from mangomas.adapters.llm import VertexClient
from mangomas.composition import (
    _build_gcp_secrets_provider,
    _file_memory_factory,
    _HarnessOrchestrator,
    _lmstudio_embedding_factory,
    _storage_registry,
    _vector_registry,
    _vertex_factory,
    agent_registry,
    build_orchestrator,
    embedding_registry,
    llm_registry,
)
from mangomas.config import (
    DEFAULT_GCP_SECRET_VERSION,
    DEFAULT_GCP_SECRETS_TIMEOUT_SECONDS,
    DBSettings,
    EmbeddingSettings,
    LLMSettings,
    MemorySettings,
    SecretsSettings,
    Settings,
)
from mangomas.core import Orchestrator
from mangomas.core.agent import AgentContext, AgentRequest, Message
from mangomas.errors import ConfigError
from mangomas.secrets import secrets_registry
from tests.constants import DEFAULT_AGENT_NAME, STUB_REPLY
from tests.fakes import (
    FakeEmbeddingClient,
    FakeLLM,
    FakeRepository,
    FakeVectorStore,
    FakeVertexGenerativeModel,
)


def _close_repo(orch: Orchestrator) -> None:
    repo = orch.context.repo
    assert repo is not None
    repo.close()


def test_build_orchestrator_wires_chat_agent() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert "chat" in orch.list_agents()
        assert orch.context.llm is not None
        assert orch.context.repo is not None
    finally:
        _close_repo(orch)


def test_build_orchestrator_wires_summarize_agent() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert "summarize" in orch.list_agents()
    finally:
        _close_repo(orch)


def test_default_agents_are_registered_in_agent_registry() -> None:
    assert "chat" in agent_registry.available()
    assert "summarize" in agent_registry.available()
    assert "tool" in agent_registry.available()
    assert "planner" in agent_registry.available()
    assert "reviewer" in agent_registry.available()


def test_build_orchestrator_disabled_harness_returns_plain_orchestrator() -> None:
    """With ``harness.enabled=False`` (default), no subclass is engaged."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert type(orch) is Orchestrator
        assert not isinstance(orch, _HarnessOrchestrator)
    finally:
        _close_repo(orch)


def test_build_orchestrator_enabled_harness_returns_wrapper() -> None:
    """With ``harness.enabled=True``, the harness subclass is returned and agents match."""
    baseline_settings = Settings(_env_file=None)  # type: ignore[call-arg]
    baseline_settings.db.url = "sqlite:///:memory:"
    baseline = build_orchestrator(baseline_settings)
    baseline_agents = baseline.list_agents()
    _close_repo(baseline)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.harness.enabled = True
    orch = build_orchestrator(settings)
    try:
        assert isinstance(orch, _HarnessOrchestrator)
        assert orch.list_agents() == baseline_agents
    finally:
        _close_repo(orch)


# ── Vertex factory (uses main's VertexClient + project_id/credentials_path) ──


def test_vertex_factory_is_registered_in_llm_registry() -> None:
    assert "vertex" in llm_registry.available()


def test_vertex_factory_forwards_settings_to_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_vertex_factory`` must forward LLMSettings fields to ``VertexClient``.

    We monkeypatch the ``VertexClient`` symbol imported by ``composition.py``
    with a recorder so the test doesn't need the real Vertex SDK.
    """
    captured: dict[str, Any] = {}

    class _Recorder:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(composition_module, "VertexClient", _Recorder)

    cfg = LLMSettings(
        provider="vertex",
        model="gemini-fake",
        project_id="proj-x",
        location="europe-west4",
        credentials_path="/keys/sa.json",
        timeout_seconds=42.0,
        temperature=0.7,
    )
    _vertex_factory(cfg)
    assert captured == {
        "project_id": "proj-x",
        "location": "europe-west4",
        "model": "gemini-fake",
        "credentials_path": "/keys/sa.json",
        "credentials_json": None,
        "timeout_seconds": 42.0,
        "default_temperature": 0.7,
    }


def test_vertex_factory_uses_resolved_secret_as_credentials_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``secret_ref`` is set, the resolved ``api_key`` becomes ``credentials_json``."""
    captured: dict[str, Any] = {}

    class _Recorder:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(composition_module, "VertexClient", _Recorder)

    cfg = LLMSettings(
        provider="vertex",
        model="gemini-fake",
        project_id="proj-x",
        secret_ref="VERTEX_SA",  # noqa: S106 — test fixture, not a real secret
        api_key='{"client_email": "x@y.z"}',  # would be the resolved secret value
    )
    _vertex_factory(cfg)
    assert captured["credentials_json"] == '{"client_email": "x@y.z"}'
    assert captured["credentials_path"] is None


def test_vertex_factory_builds_vertex_client_with_injected_model() -> None:
    """The seeded vertex factory must accept LLMSettings and yield a VertexClient.

    We swap the factory inside a :meth:`Registry.scoped` block to inject a
    fake :class:`FakeVertexGenerativeModel` so the test does not import the
    real SDK or touch the network.
    """
    fake_model = FakeVertexGenerativeModel(reply="vertex-says-hi")

    def _vertex_factory_with_fake(cfg: LLMSettings) -> VertexClient:
        return VertexClient(
            project_id=cfg.project_id,
            location=cfg.location,
            model=cfg.model,
            client=fake_model,
            timeout_seconds=cfg.timeout_seconds,
            default_temperature=cfg.temperature,
        )

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.llm.provider = "vertex"
    settings.llm.project_id = "test-project"
    settings.llm.model = "gemini-fake"
    settings.db.url = "sqlite:///:memory:"

    with llm_registry.scoped("vertex", _vertex_factory_with_fake):
        orch = build_orchestrator(settings)
        try:
            assert isinstance(orch.context.llm, VertexClient)
        finally:
            _close_repo(orch)


def test_build_orchestrator_wires_all_agents() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.memory.enabled = False
    orch = build_orchestrator(settings)
    try:
        agents = orch.list_agents()
        assert "chat" in agents
        assert "summarize" in agents
        assert "tool" in agents
        assert "planner" in agents
        assert "reviewer" in agents
        assert orch.context.memory is None
    finally:
        _close_repo(orch)


def test_build_orchestrator_with_memory_enabled_attaches_repo(tmp_path: Path) -> None:
    """The ``memory.enabled=True`` branch wires a :class:`FileMemoryRepository`."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.memory.enabled = True
    settings.memory.memory_dir = str(tmp_path / "mem")
    orch = build_orchestrator(settings)
    try:
        assert orch.context.memory is not None
    finally:
        _close_repo(orch)


def test_file_memory_factory_returns_repository(tmp_path: Path) -> None:
    """Direct factory smoke test — keeps the factory hook covered for refactors."""
    cfg = MemorySettings(
        enabled=True,
        memory_dir=str(tmp_path / "mem"),
    )
    repo = _file_memory_factory(cfg)
    assert repo is not None
    repo.close()


async def test_harness_orchestrator_dispatch_wraps_through_to_baseline() -> None:
    """The harness wrapper's dispatch returns the same content as the inner orchestrator."""
    llm = FakeLLM(reply=STUB_REPLY)
    repo = FakeRepository()
    ctx = AgentContext(llm=llm, repo=repo)

    harness_cfg = Settings(_env_file=None).harness  # type: ignore[call-arg]
    harness_cfg.enabled = True
    wrapper = _HarnessOrchestrator(ctx, harness_cfg)

    # Register an agent from the registry so we exercise the real dispatch path.
    factory = agent_registry.get(DEFAULT_AGENT_NAME)
    wrapper.register(factory(None))

    request = AgentRequest(messages=[Message(role="user", content="ping")])
    response = await wrapper.dispatch(DEFAULT_AGENT_NAME, request)

    assert response.content == STUB_REPLY
    # The wrapper persisted the turn via the inner orchestrator.
    assert len(llm.calls) == 1


async def test_harness_orchestrator_stream_dispatch_yields_tokens() -> None:
    """The streaming wrapper still emits tokens through the inner orchestrator."""
    llm = FakeLLM(reply=STUB_REPLY, chunks=["hel", "lo"])
    repo = FakeRepository()
    ctx = AgentContext(llm=llm, repo=repo)

    harness_cfg = Settings(_env_file=None).harness  # type: ignore[call-arg]
    harness_cfg.enabled = True
    wrapper = _HarnessOrchestrator(ctx, harness_cfg)

    factory = agent_registry.get(DEFAULT_AGENT_NAME)
    wrapper.register(factory(None))

    request = AgentRequest(messages=[Message(role="user", content="hi")])
    stream = await wrapper.stream_dispatch(DEFAULT_AGENT_NAME, request)
    received = [chunk async for chunk in stream]

    assert "".join(received)  # at least one non-empty token


# ── Embeddings wiring ─────────────────────────────────────────────────────────


def test_embedding_factories_registered() -> None:
    available = embedding_registry.available()
    assert "lmstudio" in available
    assert "sentence_transformers" in available
    assert "vertex" in available


def test_build_orchestrator_embeddings_disabled_by_default() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert orch.context.embeddings is None
    finally:
        _close_repo(orch)


def test_build_orchestrator_embeddings_enabled_attaches_client() -> None:
    """When ``embeddings.enabled=True`` the factory result is attached to the context."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.embeddings.enabled = True
    settings.embeddings.provider = "lmstudio"

    fake = FakeEmbeddingClient()
    with embedding_registry.scoped("lmstudio", lambda _cfg: fake):
        orch = build_orchestrator(settings)
        try:
            assert orch.context.embeddings is fake
        finally:
            _close_repo(orch)


def test_lmstudio_embedding_factory_forwards_settings() -> None:
    cfg = EmbeddingSettings(
        enabled=True,
        provider="lmstudio",
        model="embed-x",
        base_url="http://lm/v1",
        timeout_seconds=42.0,
    )
    client = _lmstudio_embedding_factory(cfg)
    assert client._model == "embed-x"
    assert client._base_url == "http://lm/v1"


# ── Vector store wiring ───────────────────────────────────────────────────────


def test_vector_factory_registered() -> None:
    assert "chroma" in _vector_registry.available()


def test_build_orchestrator_vector_disabled_by_default() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert orch.context.vector_store is None
    finally:
        _close_repo(orch)


def test_build_orchestrator_vector_enabled_attaches_store() -> None:
    """When ``vector.enabled=True`` the factory result is attached to the context."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.vector.enabled = True
    settings.vector.provider = "chroma"

    fake = FakeVectorStore()
    with _vector_registry.scoped("chroma", lambda _cfg: fake):
        orch = build_orchestrator(settings)
        try:
            assert orch.context.vector_store is fake
        finally:
            _close_repo(orch)


# ── RAG retrieval tool wiring ─────────────────────────────────────────────────


def _rag_enabled_settings() -> Settings:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.embeddings.enabled = True
    settings.embeddings.provider = "lmstudio"
    settings.vector.enabled = True
    settings.vector.provider = "chroma"
    return settings


def test_build_orchestrator_wires_retrieval_tool_when_both_enabled() -> None:
    settings = _rag_enabled_settings()
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
            _close_repo(orch)


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
            _close_repo(orch)


def test_build_orchestrator_no_tools_when_rag_disabled() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert orch.context.tools is None
    finally:
        _close_repo(orch)


# ── Postgres + GCP Secret Manager registration (v0.3.0 cloud swap-in) ────────


def test_postgres_factory_registered_in_storage_registry() -> None:
    assert "postgres" in _storage_registry.available()


def test_postgres_factory_constructs_repo_without_io() -> None:
    """The postgres factory must not open a pool — preserves the sync shape."""
    factory = _storage_registry.get("postgres")
    cfg = DBSettings(provider="postgres", url="postgresql://h/db")
    repo = factory(cfg)
    # Lazy-pool invariant from PostgresRepository.
    assert repo._pool is None


def test_gcp_secrets_lazy_registers_on_build_when_provider_selected() -> None:
    """When secrets.provider='gcp', build_orchestrator must lazy-register it."""
    settings = Settings(
        llm=LLMSettings(provider="lmstudio", api_key="inline"),
        db=DBSettings(provider="sqlite", url="sqlite:///:memory:"),
        secrets=SecretsSettings(provider="gcp", project_id="test-proj"),
    )
    # Drop any prior gcp binding so we can observe the lazy registration.
    if "gcp" in secrets_registry.available():
        secrets_registry._store.pop("gcp", None)

    captured: dict[str, Any] = {}

    def _capturing_llm_factory(cfg: LLMSettings) -> object:
        captured["api_key"] = cfg.api_key

        class _Stub:
            async def aclose(self) -> None: ...

        return _Stub()

    with llm_registry.scoped("lmstudio", _capturing_llm_factory):
        orch = build_orchestrator(settings)
        try:
            assert "gcp" in secrets_registry.available()
        finally:
            _close_repo(orch)


def test_gcp_secrets_lazy_register_raises_when_project_id_missing() -> None:
    """build_orchestrator must surface the ConfigError eagerly when misconfigured."""
    settings = Settings(
        llm=LLMSettings(provider="lmstudio", api_key="inline"),
        db=DBSettings(provider="sqlite", url="sqlite:///:memory:"),
        secrets=SecretsSettings(provider="gcp", project_id=None),
    )
    if "gcp" in secrets_registry.available():
        secrets_registry._store.pop("gcp", None)
    with pytest.raises(ConfigError, match="MANGOMAS_SECRETS__PROJECT_ID"):
        build_orchestrator(settings)


def test_build_gcp_secrets_provider_returns_provider_when_valid() -> None:
    """Success branch of _build_gcp_secrets_provider — config valid, no SDK call."""
    cfg = SecretsSettings(
        provider="gcp",
        project_id="my-proj",
        timeout_seconds=DEFAULT_GCP_SECRETS_TIMEOUT_SECONDS,
        default_version=DEFAULT_GCP_SECRET_VERSION,
    )
    provider = _build_gcp_secrets_provider(cfg)
    assert provider is not None
    assert provider._project_id == "my-proj"
    # strict defaults to False (ADR-0002 contract preserved).
    assert provider._strict is False


def test_build_gcp_secrets_provider_forwards_strict() -> None:
    """``SecretsSettings.strict`` is forwarded to the provider."""
    cfg = SecretsSettings(provider="gcp", project_id="my-proj", strict=True)
    provider = _build_gcp_secrets_provider(cfg)
    assert provider._strict is True
