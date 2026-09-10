"""Vector-store factory wiring."""

from __future__ import annotations

from typing import Any

import pytest

from mangomas.composition import _vector_registry, build_orchestrator
from mangomas.config import Settings, VectorSettings
from tests.composition.helpers import close_repo
from tests.fakes import FakeVectorStore


def test_vector_factory_registered() -> None:
    assert "chroma" in _vector_registry.available()


def test_chroma_vector_factory_forwards_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """See ``test_sentence_transformers_embedding_factory_forwards_settings`` —
    same lazy-import blind spot, same fix."""
    captured: dict[str, Any] = {}

    class _Recorder:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        "mangomas.adapters.vector.chroma.ChromaVectorStore",
        _Recorder,
    )
    from mangomas.composition.vector import _chroma_vector_factory  # noqa: PLC0415

    cfg = VectorSettings(enabled=True, provider="chroma", persist_dir="./data/x", collection="c1")
    _chroma_vector_factory(cfg)
    assert captured == {"persist_dir": "./data/x", "collection_name": "c1"}


def test_build_orchestrator_vector_disabled_by_default() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert orch.context.vector_store is None
    finally:
        close_repo(orch)


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
            close_repo(orch)
