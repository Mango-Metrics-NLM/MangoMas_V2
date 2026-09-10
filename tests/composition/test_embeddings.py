"""Embedding-client factory wiring."""

from __future__ import annotations

from typing import Any

import pytest

from mangomas.composition import (
    _lmstudio_embedding_factory,
    build_orchestrator,
    embedding_registry,
)
from mangomas.config import EmbeddingSettings, Settings
from tests.composition.helpers import close_repo
from tests.fakes import FakeEmbeddingClient


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
        close_repo(orch)


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
            close_repo(orch)


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


def test_sentence_transformers_embedding_factory_forwards_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK-backed factories are lazy-imported inside the function body, so
    only registry membership was previously exercised — kwarg-forwarding
    regressions in these bodies were invisible to the suite. Monkeypatching
    the class at its lazy-import source (rather than through the composition
    facade) mirrors ``test_vertex_factory_forwards_settings_to_client`` above,
    adapted for a factory that imports directly instead of via a facade name.
    """
    captured: dict[str, Any] = {}

    class _Recorder:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        "mangomas.adapters.embeddings.sentence_transformers.SentenceTransformersEmbeddingClient",
        _Recorder,
    )
    cfg = EmbeddingSettings(enabled=True, provider="sentence_transformers", model="all-MiniLM-L6")
    from mangomas.composition.embeddings import (  # noqa: PLC0415
        _sentence_transformers_embedding_factory,
    )

    _sentence_transformers_embedding_factory(cfg)
    # ``device`` is always forwarded, carrying ``None`` when unset (spec-0029
    # R5). The default stays byte-identical one hop further in: the client
    # omits the argument entirely when it is ``None``, so
    # ``SentenceTransformer`` is constructed exactly as before this setting
    # existed — pinned by ``test_no_device_is_passed_when_unset`` in
    # ``tests/adapters/embeddings/test_sentence_transformers.py``.
    assert captured == {"model": "all-MiniLM-L6", "device": None}


def test_vertex_embedding_factory_forwards_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _Recorder:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        "mangomas.adapters.embeddings.vertex.VertexEmbeddingClient",
        _Recorder,
    )
    cfg = EmbeddingSettings(
        enabled=True,
        provider="vertex",
        model="text-embedding-004",
        project_id="proj-x",
        location="europe-west4",
    )
    from mangomas.composition.embeddings import _vertex_embedding_factory  # noqa: PLC0415

    _vertex_embedding_factory(cfg)
    assert captured == {
        "project_id": "proj-x",
        "location": "europe-west4",
        "model": "text-embedding-004",
    }
