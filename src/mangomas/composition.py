"""Composition root: build the orchestrator with adapters wired via registries.

The module-level ``_llm_registry`` and ``_storage_registry`` are seeded once at
import time.  ``build_orchestrator`` reads ``settings.llm.provider`` and
``settings.db.provider`` to look up the appropriate factory, constructs adapters,
and registers all agents with the orchestrator.

Adding a new LLM or storage provider requires only:
  1. Implementing the corresponding Protocol (``LLMClient`` / ``TurnRepository``).
  2. Calling ``_llm_registry.register(name, factory)`` here.
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
    Settings,
    get_settings,
)
from mangomas.core import Agent, AgentContext, Orchestrator
from mangomas.registry import Registry

logger = logging.getLogger(__name__)

# ── Provider registries ────────────────────────────────────────────────────────
# Values are callables (factories) that accept the relevant sub-settings object
# and return a fully initialised adapter.

_llm_registry: Registry[Callable[[LLMSettings], Any]] = Registry("llm")
_storage_registry: Registry[Callable[[DBSettings], Any]] = Registry("storage")
_memory_registry: Registry[Callable[[MemorySettings], Any]] = Registry("memory")
AgentFactory: TypeAlias = Callable[[AgentSettings | None], Agent]
agent_registry: Registry[AgentFactory] = Registry("agent")

# ── Factory helpers ────────────────────────────────────────────────────────────


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


# Seed registries — add more providers here when needed.
_llm_registry.register("lmstudio", _lmstudio_factory)
_storage_registry.register("sqlite", _sqlite_factory)
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
        extra={"llm_provider": cfg.llm.provider, "db_provider": cfg.db.provider},
    )

    llm = _llm_registry.get(cfg.llm.provider)(cfg.llm)
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
