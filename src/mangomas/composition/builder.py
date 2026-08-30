"""Orchestrator builder and composition logic.

The main composition root: wires adapters → context → orchestrator → agents.
Reads settings to look up the matching factories in the registries; no
hardcoded adapter names in the function body.
"""

from __future__ import annotations

import logging

from mangomas.composition._registries import (
    _memory_registry,
    _storage_registry,
    _vector_registry,
    agent_registry,
    embedding_registry,
    llm_registry,
)
from mangomas.composition.embeddings import (
    _lmstudio_embedding_factory,
    _sentence_transformers_embedding_factory,
    _vertex_embedding_factory,
)
from mangomas.composition.harness import _HarnessOrchestrator
from mangomas.composition.llm import _lmstudio_factory, _vertex_factory
from mangomas.composition.memory import _file_memory_factory
from mangomas.composition.rag import _build_rag_tools
from mangomas.composition.secrets import _resolve_llm_secrets, ensure_secrets_provider
from mangomas.composition.storage import _postgres_factory, _sqlite_factory
from mangomas.composition.vector import _chroma_vector_factory
from mangomas.config import Settings, get_settings
from mangomas.core import AgentContext, Orchestrator

logger = logging.getLogger(__name__)


def _seed_registries() -> None:
    """Seed all provider registries with their factories.

    Called once during composition to register all known providers.
    This is a side-effect function that populates module-level registries.
    """
    # Seed LLM registry
    llm_registry.register("lmstudio", _lmstudio_factory)
    llm_registry.register("vertex", _vertex_factory)

    # Seed storage registry
    _storage_registry.register("sqlite", _sqlite_factory)
    _storage_registry.register("postgres", _postgres_factory)

    # Seed memory registry
    _memory_registry.register("file", _file_memory_factory)

    # Seed embedding registry
    embedding_registry.register("lmstudio", _lmstudio_embedding_factory)
    embedding_registry.register("sentence_transformers", _sentence_transformers_embedding_factory)
    embedding_registry.register("vertex", _vertex_embedding_factory)

    # Seed vector registry
    _vector_registry.register("chroma", _chroma_vector_factory)

    logger.debug("All provider registries seeded")


# Seed registries at module import time (same behavior as original composition.py)
_seed_registries()


def build_orchestrator(settings: Settings | None = None) -> Orchestrator:
    """Wire adapters → context → orchestrator → agents.

    Reads ``settings.llm.provider`` and ``settings.db.provider`` to look up
    the matching factory in the registries; no hardcoded adapter names in the
    function body. When ``settings.memory.enabled`` is ``True``, a
    :class:`~mangomas.adapters.storage.MemoryRepository` is constructed via the
    ``_memory_registry`` and attached to the :class:`~mangomas.core.AgentContext`.

    Args:
        settings: Configuration object. If None, loads from environment via
            get_settings(). Allows tests to inject custom settings.

    Returns:
        Orchestrator: Fully-initialized orchestrator with all agents registered
            and ready to dispatch requests.

    Raises:
        ConfigError: If a required setting is missing or invalid.
        AgentNotFound: If a configured provider doesn't exist in its registry.
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

    # Ensure secrets provider is registered (needed early for auth token resolution)
    ensure_secrets_provider(cfg.secrets)

    # Build LLM client with secret resolution
    llm_cfg = _resolve_llm_secrets(cfg.llm, cfg.secrets.provider)
    llm = llm_registry.get(llm_cfg.provider)(llm_cfg)
    logger.debug("LLM client built", extra={"provider": llm_cfg.provider})

    # Build storage repository
    repo = _storage_registry.get(cfg.db.provider)(cfg.db)
    logger.debug("Storage repository built", extra={"provider": cfg.db.provider})

    # Build optional memory repository
    memory = None
    if cfg.memory.enabled:
        memory = _memory_registry.get(cfg.memory.provider)(cfg.memory)
        logger.info("Memory enabled (provider=%s)", cfg.memory.provider)

    # Build optional embedding client
    embeddings = None
    if cfg.embeddings.enabled:
        embeddings = embedding_registry.get(cfg.embeddings.provider)(cfg.embeddings)
        logger.info("Embeddings enabled (provider=%s)", cfg.embeddings.provider)

    # Build optional vector store
    vector_store = None
    if cfg.vector.enabled:
        vector_store = _vector_registry.get(cfg.vector.provider)(cfg.vector)
        logger.info("Vector store enabled (provider=%s)", cfg.vector.provider)

    # When both retrieval seams are present, expose a RetrievalTool so the
    # ToolAgent auto-discovers it via ctx.tools. RAG disabled → tools stays None.
    tools = _build_rag_tools(embeddings, vector_store, cfg.vector.top_k)

    # Create agent context with all wired components
    ctx = AgentContext(
        llm=llm,
        repo=repo,
        memory=memory,
        embeddings=embeddings,
        vector_store=vector_store,
        tools=tools,
    )
    logger.debug("AgentContext created with all components")

    # Create orchestrator (harness-wrapped if enabled)
    # Wiring `cfg.loop` is what activates the control-loop tunables
    # (spec-0026): the per-step timeout and the max_steps deployment default.
    if cfg.harness.enabled:
        orch: Orchestrator = _HarnessOrchestrator(ctx, cfg.harness, loop_settings=cfg.loop)
        logger.info(
            "Harness telemetry enabled",
            extra={"metrics_namespace": cfg.harness.metrics_namespace},
        )
    else:
        orch = Orchestrator(ctx, loop_settings=cfg.loop)
        logger.debug("Orchestrator created without harness wrapper")

    # Layer in any entry-point agent plugins (no-op unless discovery_enabled).
    # Built-ins are already registered at module import time and form the protected set.
    # Import here (rather than at module level) to allow tests to monkeypatch.
    import mangomas.composition as composition_module  # noqa: PLC0415

    composition_module.ensure_agent_plugins(cfg, agent_registry)

    # Register all agents with the orchestrator
    for agent_name in agent_registry.available():
        factory = agent_registry.get(agent_name)
        agent_cfg = cfg.agents.get(agent_name)
        orch.register(factory(agent_cfg))

    logger.info("Orchestrator ready with agents: %s", orch.list_agents())
    return orch


__all__ = [
    "build_orchestrator",
]
