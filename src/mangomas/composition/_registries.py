"""Provider registries for composition factories.

Module-level registry singletons are seeded once at import time. Each registry
maps a provider name to a factory function that constructs the corresponding adapter.

Values are callables (factories) that accept the relevant sub-settings object
and return a fully initialised adapter.

``llm_registry`` and ``embedding_registry`` are exported (not underscore-prefixed)
so test suites can use :meth:`Registry.scoped` to swap a factory for the duration
of a block. Storage, memory, and vector registries remain private — tests can
provide explicit Settings instead.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeAlias

from mangomas.config import (
    DBSettings,
    EmbeddingSettings,
    LLMSettings,
    MemorySettings,
    VectorSettings,
)
from mangomas.registry import Registry

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
AgentFactory: TypeAlias = Callable[[Any], Any]
agent_registry: Registry[AgentFactory] = Registry("agent")

__all__ = [
    "AgentFactory",
    "_memory_registry",
    "_storage_registry",
    "_vector_registry",
    "agent_registry",
    "embedding_registry",
    "llm_registry",
]
