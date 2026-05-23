"""Tests for the composition root."""

from __future__ import annotations

from typing import Any

import pytest

import mangomas.composition as composition_module
from mangomas.adapters.llm import VertexClient
from mangomas.composition import (
    _vertex_factory,
    agent_registry,
    build_orchestrator,
    llm_registry,
)
from mangomas.config import LLMSettings, Settings
from mangomas.core import Orchestrator
from tests.fakes import FakeVertexGenerativeModel


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


def test_build_orchestrator_wires_memory_when_enabled(tmp_path: Any) -> None:
    """Exercise ``_file_memory_factory`` + the memory-enabled branch."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.memory.enabled = True
    settings.memory.memory_dir = str(tmp_path / "memory")
    orch = build_orchestrator(settings)
    try:
        assert orch.context.memory is not None
    finally:
        _close_repo(orch)
