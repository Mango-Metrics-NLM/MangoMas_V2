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

import logging
from collections.abc import Callable
from typing import Any, TypeAlias

from mangomas.adapters.llm import LMStudioClient
from mangomas.adapters.storage import FileMemoryRepository, SQLiteRepository
from mangomas.agents import ChatAgent, PlannerAgent, ReviewerAgent, SummarizeAgent, ToolAgent
from mangomas.config import (
    AgentSettings,
    DBSettings,
    LLMSettings,
    MemorySettings,
    SecretsSettings,
    Settings,
    get_settings,
)
from mangomas.core import Agent, AgentContext, Orchestrator
from mangomas.errors import ConfigError
from mangomas.registry import Registry
from mangomas.secrets import secrets_registry

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


def _sqlite_factory(cfg: DBSettings) -> SQLiteRepository:
    return SQLiteRepository(cfg.url)


def _file_memory_factory(cfg: MemorySettings) -> FileMemoryRepository:
    return FileMemoryRepository(cfg)


# ── Cloud factories ───────────────────────────────────────────────────────────
# Each factory lazy-imports its SDK so the optional extras (`vertex`,
# `postgres`, `gcp`) genuinely stay optional: the import only fires when the
# corresponding provider is actually selected via Settings.


def _vertex_factory(cfg: LLMSettings) -> Any:
    """Build a VertexLLMClient from LLMSettings; requires ``project``."""
    if not cfg.project:
        raise ConfigError("MANGOMAS_LLM__PROJECT is required when MANGOMAS_LLM__PROVIDER='vertex'.")
    from mangomas.adapters.llm.vertex import VertexLLMClient  # noqa: PLC0415

    return VertexLLMClient(
        project=cfg.project,
        location=cfg.location,
        model=cfg.model,
        request_timeout_seconds=cfg.timeout_seconds,
        default_temperature=cfg.temperature,
        max_output_tokens=cfg.max_output_tokens,
    )


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
    )


# Seed registries — add more providers here when needed.
llm_registry.register("lmstudio", _lmstudio_factory)
llm_registry.register("vertex", _vertex_factory)
_storage_registry.register("sqlite", _sqlite_factory)
_storage_registry.register("postgres", _postgres_factory)
_memory_registry.register("file", _file_memory_factory)

# Seed the default in-process agents.  Optional entry-point discovery can add to
# this registry later without changing build_orchestrator().
agent_registry.register("chat", lambda settings: ChatAgent(settings=settings))
agent_registry.register("summarize", lambda settings: SummarizeAgent(settings=settings))
agent_registry.register("tool", lambda settings: ToolAgent(settings=settings))
agent_registry.register("planner", lambda settings: PlannerAgent(settings=settings))
agent_registry.register("reviewer", lambda settings: ReviewerAgent(settings=settings))


# ── Public API ────────────────────────────────────────────────────────────────


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

    ctx = AgentContext(llm=llm, repo=repo, memory=memory)
    orch = Orchestrator(ctx)

    for agent_name in agent_registry.available():
        factory = agent_registry.get(agent_name)
        agent_cfg = cfg.agents.get(agent_name)
        orch.register(factory(agent_cfg))

    logger.info("Orchestrator ready with agents: %s", orch.list_agents())
    return orch
