"""Composition root: build the orchestrator with adapters wired via registries.

The module-level ``llm_registry`` and ``_storage_registry`` are seeded once at
import time.  ``build_orchestrator`` reads ``settings.llm.provider`` and
``settings.db.provider`` to look up the appropriate factory, constructs adapters,
and registers all agents with the orchestrator.

Adding a new LLM or storage provider requires only:
  1. Implementing the corresponding Protocol (``LLMClient`` / ``TurnRepository``).
  2. Calling ``llm_registry.register(name, factory)`` in the appropriate factory module.

This module is a public facade that re-exports all composition APIs, ensuring
backward compatibility. Internal imports should reference the specific submodules
(e.g., ``from mangomas.composition.llm import _lmstudio_factory``), but external
callers can use the unified ``mangomas.composition`` namespace.
"""

from __future__ import annotations

# Import agents to trigger default agent registration at module import time
# (must happen before builder is imported, or before registries are accessed)
import mangomas.composition.agents  # noqa: F401

# Imports needed for test monkeypatching
from mangomas.adapters.llm import VertexClient as VertexClient
from mangomas.agents.discovery import ensure_agent_plugins as ensure_agent_plugins

# Registries (used by tests via Registry.scoped and by builder)
from mangomas.composition._registries import (
    AgentFactory as AgentFactory,
)
from mangomas.composition._registries import (
    _memory_registry as _memory_registry,
)
from mangomas.composition._registries import (
    _storage_registry as _storage_registry,
)
from mangomas.composition._registries import (
    _vector_registry as _vector_registry,
)
from mangomas.composition._registries import (
    agent_registry as agent_registry,
)
from mangomas.composition._registries import (
    embedding_registry as embedding_registry,
)
from mangomas.composition._registries import (
    llm_registry as llm_registry,
)

# Public API (main functions)
from mangomas.composition.builder import build_orchestrator as build_orchestrator

# Embedding factories (used by tests for direct testing)
from mangomas.composition.embeddings import (
    _lmstudio_embedding_factory as _lmstudio_embedding_factory,
)
from mangomas.composition.embeddings import (
    _sentence_transformers_embedding_factory as _sentence_transformers_embedding_factory,
)
from mangomas.composition.embeddings import (
    _vertex_embedding_factory as _vertex_embedding_factory,
)

# Harness orchestrator (used by builder and tests)
from mangomas.composition.harness import (
    _HarnessOrchestrator as _HarnessOrchestrator,
)

# LLM factories (used by tests for direct testing)
from mangomas.composition.llm import (
    _AgentLLMOverrideCloseMixin as _AgentLLMOverrideCloseMixin,
)
from mangomas.composition.llm import (
    _lmstudio_factory as _lmstudio_factory,
)
from mangomas.composition.llm import (
    _vertex_factory as _vertex_factory,
)
from mangomas.composition.llm import (
    build_agent_llm_overrides as build_agent_llm_overrides,
)

# Memory factory (used by tests for direct testing)
from mangomas.composition.memory import (
    _file_memory_factory as _file_memory_factory,
)

# RAG factory (used by builder and tests)
from mangomas.composition.rag import (
    _build_rag_tools as _build_rag_tools,
)

# Secrets resolution (used by builder and tests)
from mangomas.composition.secrets import (
    _build_gcp_secrets_provider as _build_gcp_secrets_provider,
)
from mangomas.composition.secrets import (
    _resolve_llm_secrets as _resolve_llm_secrets,
)
from mangomas.composition.secrets import (
    ensure_secrets_provider as ensure_secrets_provider,
)
from mangomas.composition.signal import (
    _attach_cognitive_extras as _attach_cognitive_extras,
)

# Storage factories (used by tests for direct testing)
from mangomas.composition.storage import (
    _postgres_factory as _postgres_factory,
)
from mangomas.composition.storage import (
    _sqlite_factory as _sqlite_factory,
)

# Vector factory (used by tests for direct testing)
from mangomas.composition.vector import (
    _chroma_vector_factory as _chroma_vector_factory,
)
from mangomas.secrets import secrets_registry as secrets_registry

__all__ = [
    "AgentFactory",
    "VertexClient",
    "_AgentLLMOverrideCloseMixin",
    "_HarnessOrchestrator",
    "_attach_cognitive_extras",
    "_build_gcp_secrets_provider",
    "_build_rag_tools",
    "_chroma_vector_factory",
    "_file_memory_factory",
    "_lmstudio_embedding_factory",
    "_lmstudio_factory",
    "_memory_registry",
    "_postgres_factory",
    "_resolve_llm_secrets",
    "_sentence_transformers_embedding_factory",
    "_sqlite_factory",
    "_storage_registry",
    "_vector_registry",
    "_vertex_embedding_factory",
    "_vertex_factory",
    "agent_registry",
    "build_agent_llm_overrides",
    "build_orchestrator",
    "embedding_registry",
    "ensure_agent_plugins",
    "ensure_secrets_provider",
    "llm_registry",
    "secrets_registry",
]
