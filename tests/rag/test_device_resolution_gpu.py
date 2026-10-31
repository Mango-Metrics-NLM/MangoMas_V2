"""Tier-3 GPU acceptance: the configured device is the device that actually runs.

``tests/rag/test_cli_local_round_trip.py`` proves the auto-detect and
forced-CPU modes *retrieve* correctly, but nothing asserted which device the
model really landed on: on a CUDA box a silent CPU fallback passed every check
(spec-0039). That fallback is not hypothetical: installing ``.[dev,...]`` over
a ``+cu128`` torch on Windows swapped it for PyPI's CPU-only wheel, because the
cu128 build caps ``setuptools<82`` and ``[dev]`` floors ``setuptools>=83``.
This module pins the resolution itself, plus ranking parity against CPU for
every CUDA device present.

Gating: the CPU test carries ``@pytest.mark.embeddings_local``
(``RUN_EMBEDDINGS_LOCAL=1``). The CUDA tests carry ``@pytest.mark.gpu``
(``RUN_GPU=1``), an explicit operator assertion that a GPU is present. With
that gate set, a missing device FAILS rather than skips: a runtime ``skipif``
both trips the zero-skip guard on CPU-only runners and hides the exact
regression this module exists for (a GPU box whose torch quietly became a CPU
wheel). Device ids are derived from ``torch.cuda.device_count()`` only when
``RUN_GPU=1`` so default collection touches no device; nothing assumes a
particular box.
"""

from __future__ import annotations

import math
import os
from collections.abc import Sequence

import pytest

from mangomas.adapters.embeddings.sentence_transformers import (
    SentenceTransformersEmbeddingClient,
)
from tests.constants import (
    DEFAULT_LOCAL_EMBEDDING_MODEL,
    DEVICE_CPU,
    DEVICE_CUDA,
    ENV_FLAG_ON,
    LOCAL_EMBEDDING_MODEL_ENV,
    RAG_DEVICE_CORPUS,
    RAG_DEVICE_EXPECTED_TOP_SOURCE,
    RAG_DEVICE_QUERY,
    RUN_GPU_ENV,
)


def _cuda_device_count() -> int:
    """Visible CUDA devices when ``RUN_GPU=1``; 0 otherwise or when torch is absent.

    Short-circuits when ``RUN_GPU`` is unset so default ``pytest`` collection
    touches no device and never imports ``torch``. Not ``pytest.importorskip``:
    a module-level skip is an unsanctioned collection outcome on the
    ``[dev]``-only CI population (zero-skip guard), and with ``RUN_GPU=1`` a
    missing torch must fail like any other missing GPU.
    """
    if os.getenv(RUN_GPU_ENV) != ENV_FLAG_ON:
        return 0
    try:
        import torch  # noqa: PLC0415 — optional embeddings-local dependency
    except ImportError:
        return 0
    return int(torch.cuda.device_count()) if torch.cuda.is_available() else 0


_CUDA_COUNT: int = _cuda_device_count()
_NO_CUDA_MESSAGE = (
    f"{RUN_GPU_ENV}={ENV_FLAG_ON} asserts a CUDA device, but torch sees none "
    "(torch missing, CPU-only torch wheel, driver fault, or CUDA_VISIBLE_DEVICES hiding them)"
)


def _require_cuda() -> None:
    assert _CUDA_COUNT > 0, _NO_CUDA_MESSAGE


def _model_name() -> str:
    return os.getenv(LOCAL_EMBEDDING_MODEL_ENV, DEFAULT_LOCAL_EMBEDDING_MODEL)


def _resolved_device(client: SentenceTransformersEmbeddingClient) -> object:
    # White-box by necessity: the adapter's public surface is embed/embed_batch,
    # and the device a SentenceTransformer landed on is only observable on it.
    return client._model.device


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


async def _top_source(client: SentenceTransformersEmbeddingClient) -> str:
    names = list(RAG_DEVICE_CORPUS)
    vectors = await client.embed_batch([RAG_DEVICE_CORPUS[n] for n in names])
    query = await client.embed(RAG_DEVICE_QUERY)
    scores = {name: _cosine(query, vec) for name, vec in zip(names, vectors, strict=True)}
    return max(scores, key=scores.__getitem__)


@pytest.mark.gpu
async def test_auto_detect_lands_on_cuda_when_present() -> None:
    """``device=None`` is the operator default; with CUDA present it must use it."""
    _require_cuda()
    client = SentenceTransformersEmbeddingClient(_model_name())
    try:
        assert getattr(_resolved_device(client), "type", None) == DEVICE_CUDA
    finally:
        await client.aclose()


@pytest.mark.gpu
# max(..., 1): an empty parameter set is itself a skip; index 0 then fails
# loudly through _require_cuda() instead.
@pytest.mark.parametrize("index", range(max(_CUDA_COUNT, 1)))
async def test_forced_cuda_index_lands_on_that_device_with_cpu_ranking_parity(
    index: int,
) -> None:
    """Every present GPU: correct placement and the same top hit as CPU."""
    _require_cuda()
    forced = f"{DEVICE_CUDA}:{index}"
    gpu = SentenceTransformersEmbeddingClient(_model_name(), device=forced)
    try:
        device = _resolved_device(gpu)
        assert getattr(device, "type", None) == DEVICE_CUDA
        assert getattr(device, "index", None) == index
        cpu = SentenceTransformersEmbeddingClient(_model_name(), device=DEVICE_CPU)
        try:
            assert (
                await _top_source(gpu) == await _top_source(cpu) == RAG_DEVICE_EXPECTED_TOP_SOURCE
            )
        finally:
            await cpu.aclose()
    finally:
        await gpu.aclose()


@pytest.mark.embeddings_local
async def test_forced_cpu_stays_on_cpu() -> None:
    """The CPU knob is honoured even on a GPU box (the nightly-runner repro)."""
    client = SentenceTransformersEmbeddingClient(_model_name(), device=DEVICE_CPU)
    try:
        assert getattr(_resolved_device(client), "type", None) == DEVICE_CPU
    finally:
        await client.aclose()
