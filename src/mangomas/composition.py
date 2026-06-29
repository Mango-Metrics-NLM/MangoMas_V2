"""Composition root: build the orchestrator with adapters wired via registries.

The module-level ``llm_registry`` and ``_storage_registry`` are seeded once at
import time.  ``build_orchestrator`` reads ``settings.llm.provider`` and
``settings.db.provider`` to look up the appropriate factory, constructs adapters,
and registers all agents with the orchestrator.

Adding a new LLM or storage provider requires only:
  1. Implementing the corresponding Protocol (``LLMClient`` / ``TurnRepository``).
  2. Calling ``llm_registry.register(name, factory)`` here.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any, TypeAlias

from mangomas.adapters.embeddings import LMStudioEmbeddingClient
from mangomas.adapters.llm import LMStudioClient, VertexClient
from mangomas.adapters.storage import FileMemoryRepository, SQLiteRepository
from mangomas.agents import ChatAgent, PlannerAgent, ReviewerAgent, SummarizeAgent, ToolAgent
from mangomas.agents.discovery import ensure_agent_plugins
from mangomas.config import (
    AgentSettings,
    DBSettings,
    EmbeddingSettings,
    HarnessSettings,
    LLMSettings,
    MemorySettings,
    SecretsSettings,
    Settings,
    VectorSettings,
    get_settings,
)
from mangomas.core import Agent, AgentContext, Orchestrator
from mangomas.core.agent import AgentRequest, AgentResponse
from mangomas.core.loop import AcceptanceFn
from mangomas.core.tools import ToolRegistry
from mangomas.errors import ConfigError
from mangomas.registry import Registry
from mangomas.secrets import secrets_registry
from mangomas.telemetry import get_tracer

if TYPE_CHECKING:  # pragma: no cover
    from opentelemetry.sdk.trace import TracerProvider

logger = logging.getLogger(__name__)

# ── Provider registries ────────────────────────────────────────────────────────
# Values are callables (factories) that accept the relevant sub-settings object
# and return a fully initialised adapter.
#
# ``llm_registry`` is exported (not underscore-prefixed) so test suites can use
# :meth:`Registry.scoped` to swap an LLM factory for the duration of a block
# (e.g. for streaming-fallback exercises). Storage and memory registries remain
# private — tests can provide explicit Settings instead.

llm_registry: Registry[Callable[[LLMSettings], Any]] = Registry("llm")
_storage_registry: Registry[Callable[[DBSettings], Any]] = Registry("storage")
_memory_registry: Registry[Callable[[MemorySettings], Any]] = Registry("memory")
# Exported (like ``llm_registry``) so tests can swap an embedding factory via
# ``Registry.scoped`` for the duration of a block.
embedding_registry: Registry[Callable[[EmbeddingSettings], Any]] = Registry("embeddings")
# Private — the only backend (chroma) lazy-imports its SDK, so tests swap the
# factory via ``_vector_registry.scoped`` rather than installing the extra.
_vector_registry: Registry[Callable[[VectorSettings], Any]] = Registry("vector")
AgentFactory: TypeAlias = Callable[[AgentSettings | None], Agent]
agent_registry: Registry[AgentFactory] = Registry("agent")

# ── Factory helpers ────────────────────────────────────────────────────────────


def _resolve_llm_secrets(llm_cfg: LLMSettings, secrets_provider_name: str) -> LLMSettings:
    """Return *llm_cfg* with ``api_key`` resolved via the SecretsProvider seam.

    When ``llm_cfg.secret_ref`` is unset, *llm_cfg* is returned unchanged. When
    set, the named secrets provider is looked up and its ``get(secret_ref)``
    return value replaces ``api_key`` on a fresh copy of the settings model.
    If the provider returns ``None`` the inline ``api_key`` is kept — local
    development with no env var configured continues to work without a vault.
    """
    if not llm_cfg.secret_ref:
        return llm_cfg
    provider = secrets_registry.get(secrets_provider_name)
    resolved = provider.get(llm_cfg.secret_ref)
    if resolved is None:
        logger.debug(
            "secret_ref %r not resolved by provider %r; keeping inline api_key",
            llm_cfg.secret_ref,
            secrets_provider_name,
        )
        return llm_cfg
    return llm_cfg.model_copy(update={"api_key": resolved})


def _lmstudio_factory(cfg: LLMSettings) -> LMStudioClient:
    return LMStudioClient(
        base_url=cfg.base_url,
        model=cfg.model,
        api_key=cfg.api_key,
        timeout_seconds=cfg.timeout_seconds,
        default_temperature=cfg.temperature,
    )


def _vertex_factory(cfg: LLMSettings) -> VertexClient:
    """Build a :class:`VertexClient` from :class:`LLMSettings`.

    When ``secret_ref`` is set, ``_resolve_llm_secrets`` has already replaced
    ``api_key`` with the resolved secret payload — for Vertex this is treated
    as a service-account JSON body and forwarded as ``credentials_json``.
    Otherwise the factory falls back to ``credentials_path`` (or Application
    Default Credentials when both are absent).
    """
    credentials_json = cfg.api_key if cfg.secret_ref else None
    return VertexClient(
        project_id=cfg.project_id,
        location=cfg.location,
        model=cfg.model,
        credentials_path=cfg.credentials_path,
        credentials_json=credentials_json,
        timeout_seconds=cfg.timeout_seconds,
        default_temperature=cfg.temperature,
    )


def _lmstudio_embedding_factory(cfg: EmbeddingSettings) -> LMStudioEmbeddingClient:
    return LMStudioEmbeddingClient(
        base_url=cfg.base_url,
        model=cfg.model,
        api_key=cfg.api_key,
        timeout_seconds=cfg.timeout_seconds,
    )


def _sentence_transformers_embedding_factory(cfg: EmbeddingSettings) -> Any:
    """Build a SentenceTransformersEmbeddingClient (lazy heavy import inside)."""
    from mangomas.adapters.embeddings.sentence_transformers import (  # noqa: PLC0415
        SentenceTransformersEmbeddingClient,
    )

    return SentenceTransformersEmbeddingClient(model=cfg.model)


def _vertex_embedding_factory(cfg: EmbeddingSettings) -> Any:
    """Build a VertexEmbeddingClient (ADC auth; SDK deferred inside the client)."""
    from mangomas.adapters.embeddings.vertex import VertexEmbeddingClient  # noqa: PLC0415

    return VertexEmbeddingClient(
        project_id=cfg.project_id,
        location=cfg.location,
        model=cfg.model,
    )


def _chroma_vector_factory(cfg: VectorSettings) -> Any:
    """Build a ChromaVectorStore (lazy chromadb import inside the client)."""
    from mangomas.adapters.vector.chroma import ChromaVectorStore  # noqa: PLC0415

    return ChromaVectorStore(persist_dir=cfg.persist_dir, collection_name=cfg.collection)


def _build_rag_tools(
    embeddings: Any | None,
    vector_store: Any | None,
    top_k: int,
) -> ToolRegistry | None:
    """Return a ToolRegistry holding a RetrievalTool, or ``None`` if RAG is off.

    Requires *both* an embedding client and a vector store — retrieval needs to
    embed the query and search the index. When either is absent, no tools are
    wired and ``ctx.tools`` stays ``None`` (unchanged default behaviour).
    """
    if embeddings is None or vector_store is None:
        return None
    from mangomas.rag import RetrievalTool, Retriever  # noqa: PLC0415

    retriever = Retriever(embeddings=embeddings, vector_store=vector_store, top_k=top_k)
    tool = RetrievalTool(retriever)
    registry: ToolRegistry = Registry("tool")
    registry.register(tool.name, tool)
    logger.info("RAG retrieval tool registered (top_k=%d)", top_k)
    return registry


def _sqlite_factory(cfg: DBSettings) -> SQLiteRepository:
    return SQLiteRepository(cfg.url)


def _file_memory_factory(cfg: MemorySettings) -> FileMemoryRepository:
    return FileMemoryRepository(cfg)


# ── Cloud factories (Postgres + GCP Secrets) ──────────────────────────────────
# These factories lazy-import their SDKs so the optional extras (``postgres``,
# ``gcp``) genuinely stay optional: the import only fires when the corresponding
# provider is selected via Settings. (Vertex follows the same discipline above,
# in ``_vertex_factory`` — the SDK import is deferred inside ``VertexClient``.)


def _postgres_factory(cfg: DBSettings) -> Any:
    """Build a PostgresRepository from DBSettings (asyncpg-backed)."""
    from mangomas.adapters.storage.postgres import PostgresRepository  # noqa: PLC0415

    return PostgresRepository(cfg)


def _build_gcp_secrets_provider(cfg: SecretsSettings) -> Any:
    """Build a GCPSecretManagerProvider from SecretsSettings; requires ``project_id``."""
    if not cfg.project_id:
        raise ConfigError(
            "MANGOMAS_SECRETS__PROJECT_ID is required when MANGOMAS_SECRETS__PROVIDER='gcp'."
        )
    from mangomas.secrets.gcp import GCPSecretManagerProvider  # noqa: PLC0415

    return GCPSecretManagerProvider(
        project_id=cfg.project_id,
        timeout_seconds=cfg.timeout_seconds,
        default_version=cfg.default_version,
        strict=cfg.strict,
    )


# Seed registries — add more providers here when needed.
llm_registry.register("lmstudio", _lmstudio_factory)
llm_registry.register("vertex", _vertex_factory)
_storage_registry.register("sqlite", _sqlite_factory)
_storage_registry.register("postgres", _postgres_factory)
_memory_registry.register("file", _file_memory_factory)
embedding_registry.register("lmstudio", _lmstudio_embedding_factory)
embedding_registry.register("sentence_transformers", _sentence_transformers_embedding_factory)
embedding_registry.register("vertex", _vertex_embedding_factory)
_vector_registry.register("chroma", _chroma_vector_factory)

# Seed the default in-process agents.  Optional entry-point discovery can add to
# this registry later without changing build_orchestrator().
agent_registry.register("chat", lambda settings: ChatAgent(settings=settings))
agent_registry.register("summarize", lambda settings: SummarizeAgent(settings=settings))
agent_registry.register("tool", lambda settings: ToolAgent(settings=settings))
agent_registry.register("planner", lambda settings: PlannerAgent(settings=settings))
agent_registry.register("reviewer", lambda settings: ReviewerAgent(settings=settings))


# ── Public API ────────────────────────────────────────────────────────────────


_HARNESS_SPAN_NAME = "harness.agent_invoke"
_HARNESS_TOPOLOGY_DISPATCH = "dispatch"
_HARNESS_TOPOLOGY_STREAM = "stream"


class _HarnessOrchestrator(Orchestrator):
    """Orchestrator subclass that wraps dispatch paths in a harness-level span.

    Engaged only when ``Settings.harness.enabled`` is ``True``. The parent
    span sits above the existing ``orchestrator.*`` spans so operators can
    filter or alert on agent invocations at the harness layer without
    disturbing the in-orchestrator instrumentation. Because
    ``dispatch_pipeline`` and ``dispatch_fan_out`` delegate through
    ``dispatch``, those topologies inherit the wrap automatically;
    ``stream_dispatch`` does not, so it is wrapped explicitly below.
    """

    def __init__(self, ctx: AgentContext, harness_cfg: HarnessSettings) -> None:
        super().__init__(ctx)
        # The dedicated provider (when routing is enabled) is owned by this
        # orchestrator and shut down in ``aclose`` to avoid leaking a
        # BatchSpanProcessor worker thread.
        self._harness_provider: TracerProvider | None = None
        if harness_cfg.metrics_exporter is None:
            # Default path: harness spans share the global application tracer.
            self._harness_tracer = get_tracer(harness_cfg.metrics_namespace)
        else:
            # Route harness spans to a dedicated, isolated provider.
            from mangomas.telemetry_exporters import build_harness_provider  # noqa: PLC0415

            self._harness_provider = build_harness_provider(harness_cfg)
            self._harness_tracer = self._harness_provider.get_tracer(harness_cfg.metrics_namespace)
        self._harness_cfg = harness_cfg
        logger.debug(
            "Harness orchestrator engaged",
            extra={
                "metrics_namespace": harness_cfg.metrics_namespace,
                "metrics_exporter": harness_cfg.metrics_exporter,
                "hook_log_level": harness_cfg.hook_log_level,
            },
        )

    async def aclose(self) -> None:
        """Close inner resources, then shut down the dedicated harness provider.

        ``super().aclose()`` runs first (and may re-raise); the harness provider
        shutdown runs in a ``finally`` so the worker thread is always released.
        Shutdown is offloaded to a thread because ``BatchSpanProcessor.shutdown``
        flushes and joins synchronously.
        """
        try:
            await super().aclose()
        finally:
            provider = self._harness_provider
            if provider is not None:
                self._harness_provider = None
                await asyncio.to_thread(provider.shutdown)

    async def dispatch(
        self,
        agent_name: str,
        request: AgentRequest,
        *,
        acceptance_fn: AcceptanceFn | None = None,
        max_steps: int | None = None,
    ) -> AgentResponse:
        with self._harness_tracer.start_as_current_span(_HARNESS_SPAN_NAME) as span:
            span.set_attribute("agent.name", agent_name)
            span.set_attribute("harness.topology", _HARNESS_TOPOLOGY_DISPATCH)
            span.set_attribute("messages.count", len(request.messages))
            logger.debug(
                "Harness wrapping dispatch",
                extra={"agent": agent_name, "messages": len(request.messages)},
            )
            return await super().dispatch(
                agent_name,
                request,
                acceptance_fn=acceptance_fn,
                max_steps=max_steps,
            )

    async def stream_dispatch(
        self,
        agent_name: str,
        request: AgentRequest,
    ) -> AsyncIterator[str]:
        """Wrap streaming dispatch so the harness parent span covers token emission too."""
        with self._harness_tracer.start_as_current_span(_HARNESS_SPAN_NAME) as span:
            span.set_attribute("agent.name", agent_name)
            span.set_attribute("harness.topology", _HARNESS_TOPOLOGY_STREAM)
            span.set_attribute("messages.count", len(request.messages))
            logger.debug(
                "Harness wrapping stream_dispatch",
                extra={"agent": agent_name, "messages": len(request.messages)},
            )
            return await super().stream_dispatch(agent_name, request)


def build_orchestrator(settings: Settings | None = None) -> Orchestrator:
    """Wire adapters → context → orchestrator → agents.

    Reads ``settings.llm.provider`` and ``settings.db.provider`` to look up
    the matching factory in the registries; no hardcoded adapter names in the
    function body.  When ``settings.memory.enabled`` is ``True``, a
    :class:`~mangomas.adapters.storage.MemoryRepository` is constructed via the
    ``_memory_registry`` and attached to the :class:`~mangomas.core.AgentContext`.
    """
    cfg = settings or get_settings()
    logger.info(
        "Building orchestrator",
        extra={
            "llm_provider": cfg.llm.provider,
            "db_provider": cfg.db.provider,
            "secrets_provider": cfg.secrets.provider,
        },
    )

    # Lazy-register the GCP secrets provider if selected. Idempotent — keeps
    # the env backend's "stored as instance, not factory" registry contract.
    if cfg.secrets.provider == "gcp" and "gcp" not in secrets_registry.available():
        secrets_registry.register("gcp", _build_gcp_secrets_provider(cfg.secrets))

    llm_cfg = _resolve_llm_secrets(cfg.llm, cfg.secrets.provider)
    llm = llm_registry.get(llm_cfg.provider)(llm_cfg)
    repo = _storage_registry.get(cfg.db.provider)(cfg.db)

    memory = None
    if cfg.memory.enabled:
        memory = _memory_registry.get(cfg.memory.provider)(cfg.memory)
        logger.info("Memory enabled (provider=%s)", cfg.memory.provider)

    embeddings = None
    if cfg.embeddings.enabled:
        embeddings = embedding_registry.get(cfg.embeddings.provider)(cfg.embeddings)
        logger.info("Embeddings enabled (provider=%s)", cfg.embeddings.provider)

    vector_store = None
    if cfg.vector.enabled:
        vector_store = _vector_registry.get(cfg.vector.provider)(cfg.vector)
        logger.info("Vector store enabled (provider=%s)", cfg.vector.provider)

    # When both retrieval seams are present, expose a RetrievalTool so the
    # ToolAgent auto-discovers it via ctx.tools. RAG disabled → tools stays None.
    tools = _build_rag_tools(embeddings, vector_store, cfg.vector.top_k)

    ctx = AgentContext(
        llm=llm,
        repo=repo,
        memory=memory,
        embeddings=embeddings,
        vector_store=vector_store,
        tools=tools,
    )
    if cfg.harness.enabled:
        orch: Orchestrator = _HarnessOrchestrator(ctx, cfg.harness)
        logger.info(
            "Harness telemetry enabled",
            extra={"metrics_namespace": cfg.harness.metrics_namespace},
        )
    else:
        orch = Orchestrator(ctx)

    # Optional entry-point discovery layers third-party agents into the registry
    # before wiring. No-op unless ``MANGOMAS_DISCOVERY_ENABLED=true``.
    ensure_agent_plugins(cfg, agent_registry)
    for agent_name in agent_registry.available():
        factory = agent_registry.get(agent_name)
        agent_cfg = cfg.agents.get(agent_name)
        orch.register(factory(agent_cfg))

    logger.info("Orchestrator ready with agents: %s", orch.list_agents())
    return orch
