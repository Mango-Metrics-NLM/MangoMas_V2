"""Tests for the sentence-transformers embedding adapter (injected fake model)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

import mangomas.adapters.embeddings.sentence_transformers as st_module
from mangomas.adapters.embeddings.sentence_transformers import (
    SentenceTransformersEmbeddingClient,
)
from mangomas.composition.embeddings import _sentence_transformers_embedding_factory
from mangomas.config import EmbeddingSettings
from tests.constants import (
    DEFAULT_EMBEDDINGS_DEVICE,
    DEFAULT_EMBEDDINGS_MODEL,
    DEVICE_CPU,
)
from tests.constants import (
    DEFAULT_LOCAL_EMBEDDING_MODEL as LOCAL_MODEL,
)


@dataclass
class _FakeModel:
    """Mimics ``SentenceTransformer.encode`` with the progress-bar kwarg."""

    calls: list[list[str]] = field(default_factory=list)

    def encode(
        self, texts: list[str], *, show_progress_bar: bool = False
    ) -> list[list[float]]:
        assert show_progress_bar is False
        self.calls.append(list(texts))
        # Deterministic: each row is the char ordinals of its text.
        return [[float(ord(c)) for c in t] or [0.0] for t in texts]


async def test_embed_batch_uses_injected_model() -> None:
    model = _FakeModel()
    client = SentenceTransformersEmbeddingClient(model=DEFAULT_EMBEDDINGS_MODEL, client=model)
    out = await client.embed_batch(["ab", "c"])
    assert out == [[97.0, 98.0], [99.0]]
    assert model.calls == [["ab", "c"]]


async def test_embed_single_returns_first_vector() -> None:
    model = _FakeModel()
    client = SentenceTransformersEmbeddingClient(model=DEFAULT_EMBEDDINGS_MODEL, client=model)
    out = await client.embed("a")
    assert out == [97.0]


async def test_aclose_is_noop() -> None:
    client = SentenceTransformersEmbeddingClient(
        model=DEFAULT_EMBEDDINGS_MODEL, client=_FakeModel()
    )
    await client.aclose()  # no raise


# ── Device forwarding (spec-0029 R5) ──────────────────────────────────────────
#
# Asserted through the *composition factory*, not just the client constructor,
# so both hops are covered by one test: `EmbeddingSettings.device` → factory →
# client → loader. Testing the constructor alone would leave the factory free
# to drop the argument silently, which is the more likely bug of the two.


@dataclass
class _RecordingLoader:
    """Stands in for ``_lazy_load_model``, recording how it was called.

    A recorder rather than the real loader because the real one needs the
    optional ``embeddings-local`` extra — and this repo's whole point in
    testing the seam is that the wiring is checkable without it.
    """

    calls: list[tuple[str, str | None]] = field(default_factory=list)

    def __call__(self, model_name: str, device: str | None = None) -> _FakeModel:
        self.calls.append((model_name, device))
        return _FakeModel()


def _load_via_factory(
    monkeypatch: pytest.MonkeyPatch, device: str | None
) -> list[tuple[str, str | None]]:
    """Build a client through the composition factory, returning loader calls."""
    loader = _RecordingLoader()
    monkeypatch.setattr(st_module, "_lazy_load_model", loader)
    _sentence_transformers_embedding_factory(
        EmbeddingSettings(provider="sentence_transformers", model=LOCAL_MODEL, device=device)
    )
    return loader.calls


def test_device_is_forwarded_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured device reaches the loader verbatim."""
    assert _load_via_factory(monkeypatch, DEVICE_CPU) == [(LOCAL_MODEL, DEVICE_CPU)]


def test_no_device_is_passed_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default-off: the loader sees ``None``, i.e. the library auto-detects.

    The backwards-compatibility direction. Without it, a change that hardcoded
    a device would pass the test above while silently pinning every existing
    deployment to it.
    """
    assert _load_via_factory(monkeypatch, None) == [(LOCAL_MODEL, None)]


def test_settings_default_leaves_the_device_unset() -> None:
    """The field's own default is ``None`` — asserted against the model, not a literal.

    Pins the claim `.env.example` and CLAUDE.md make. A default that drifted to
    a device string would change behaviour for every deployment that never set
    the variable.
    """
    assert EmbeddingSettings().device is DEFAULT_EMBEDDINGS_DEVICE
    assert DEFAULT_EMBEDDINGS_DEVICE is None
