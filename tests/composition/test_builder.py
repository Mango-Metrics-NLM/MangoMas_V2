"""Builder / registry wiring for the composition root."""

from __future__ import annotations

import importlib
from typing import Any

import pytest

import mangomas.composition as composition_module
from mangomas.composition import (
    _HarnessOrchestrator,
    _registries,
    _storage_registry,
    _vector_registry,
    agent_registry,
    build_orchestrator,
    embedding_registry,
    llm_registry,
)
from mangomas.config import Settings
from mangomas.core import Orchestrator
from tests.composition.helpers import close_repo


def test_build_orchestrator_wires_chat_agent() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert "chat" in orch.list_agents()
        assert orch.context.llm is not None
        assert orch.context.repo is not None
    finally:
        close_repo(orch)


def test_build_orchestrator_wires_summarize_agent() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert "summarize" in orch.list_agents()
    finally:
        close_repo(orch)


def test_build_orchestrator_invokes_agent_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_orchestrator layers in entry-point agents via ensure_agent_plugins."""
    seen: list[tuple[Any, Any]] = []

    def _spy(settings: Any, registry: Any) -> None:
        seen.append((settings, registry))

    monkeypatch.setattr(composition_module, "ensure_agent_plugins", _spy)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert len(seen) == 1
        called_settings, called_registry = seen[0]
        assert called_settings is settings
        assert called_registry is agent_registry
    finally:
        close_repo(orch)


def test_default_agents_are_registered_in_agent_registry() -> None:
    assert "chat" in agent_registry.available()
    assert "summarize" in agent_registry.available()
    assert "tool" in agent_registry.available()
    assert "planner" in agent_registry.available()
    assert "reviewer" in agent_registry.available()


def test_registry_singletons_are_the_same_object_builder_reads() -> None:
    """Pin the invariant `Registry.scoped()` monkeypatching depends on.

    The facade re-exports registries from ``_registries.py``; ``builder.py``
    imports the very same names. If a future refactor ever gave ``builder.py``
    its own ``Registry("vector")`` instead of importing the shared singleton,
    every ``*_registry.scoped(...)`` test would still run — and would fail
    with a confusing "used the real SDK" or "fake not found" error instead of
    a clear identity mismatch. This test makes the failure legible instead.
    """
    # `builder.py` deliberately does not re-export these (it is not a facade),
    # so mypy's `--no-implicit-reexport` refuses static attribute access;
    # `importlib` + `getattr` reach the same real module attribute at runtime
    # without mypy statically checking it against builder's typed exports —
    # the same idiom `tests/test_import_compat.py` uses throughout.
    builder_mod = importlib.import_module("mangomas.composition.builder")
    assert llm_registry is _registries.llm_registry is builder_mod.llm_registry
    assert embedding_registry is _registries.embedding_registry is builder_mod.embedding_registry
    assert agent_registry is _registries.agent_registry is builder_mod.agent_registry
    assert _storage_registry is _registries._storage_registry
    assert _vector_registry is _registries._vector_registry


def test_build_orchestrator_disabled_harness_returns_plain_orchestrator() -> None:
    """With ``harness.enabled=False`` (default), the harness subclass is not engaged.

    Since spec-0028, this branch still isn't a *bare* ``Orchestrator`` — it's
    ``_Orchestrator``, which mixes in ``_AgentLLMOverrideCloseMixin`` so
    per-agent MODEL_OVERRIDE clients get closed regardless of harness state.
    What this test actually pins is "no harness wrapper," not "no wrapper at
    all" — verified via ``isinstance``, not exact ``type()`` identity.
    """
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert isinstance(orch, Orchestrator)
        assert not isinstance(orch, _HarnessOrchestrator)
    finally:
        close_repo(orch)


def test_build_orchestrator_enabled_harness_returns_wrapper() -> None:
    """With ``harness.enabled=True``, the harness subclass is returned and agents match."""
    baseline_settings = Settings(_env_file=None)  # type: ignore[call-arg]
    baseline_settings.db.url = "sqlite:///:memory:"
    baseline = build_orchestrator(baseline_settings)
    baseline_agents = baseline.list_agents()
    close_repo(baseline)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.harness.enabled = True
    orch = build_orchestrator(settings)
    try:
        assert isinstance(orch, _HarnessOrchestrator)
        assert orch.list_agents() == baseline_agents
    finally:
        close_repo(orch)


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
        close_repo(orch)
