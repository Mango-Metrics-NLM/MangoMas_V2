"""Gated tier-3 RAG suite: real sentence-transformers + ephemeral Chroma.

Skipped unless BOTH ``RUN_EMBEDDINGS_LOCAL=1`` and ``RUN_RAG=1`` are set (the
``embeddings_local`` and ``rag`` markers each gate on their env var), because
it requires the ``embeddings-local`` and ``rag`` extras.

**This is the only suite in the repository that runs real numerical compute**,
so it is the only one where the device matters. Everything here obeys the
spec-0029 R2 hardware contract, and the two rules that bind hardest are:

* **Rankings, not scores** (R2.2). Two devices reduce float32 sums in
  different orders, so identical inputs give similarity scores that differ in
  the last few decimal places. Which chunk wins does not change; *by how much*
  does. Every oracle here is an ordering or a tolerance, never an equality.
* **No device literals** (R2.4). Devices come from ``tests.constants``, and
  forcing CPU is a *comparison*, never a precondition — a test that only ran
  on CPU would prove nothing about the GPU path.

``tests/tooling/test_e2e_hardware_contract.py`` lints the mechanically
checkable half of that.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import pytest

from mangomas.config import RagSettings
from mangomas.rag import IngestionPipeline, Retriever
from tests.constants import (
    DEFAULT_LOCAL_EMBEDDING_MODEL,
    DEVICE_CPU,
    EMBEDDING_COSINE_ATOL,
    EMBEDDING_SELF_COSINE,
    RAG_DEVICE_CORPUS,
    RAG_DEVICE_EXPECTED_TOP_SOURCE,
    RAG_DEVICE_QUERY,
)

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.embeddings_local, pytest.mark.rag]

# Small enough that the fixed corpus is not truncated, large enough that each
# document stays one chunk — so "which chunk ranked first" maps to "which
# document ranked first" and the oracle is legible.
_CHUNK_WORDS = 50
_CHUNK_OVERLAP = 10
_BATCH_SIZE = 8


def _cosine(left: list[float], right: list[float]) -> float:
    """Cosine similarity, computed here rather than imported.

    Deliberately dependency-free: pulling in numpy just to compare two vectors
    would add an import this suite does not otherwise need, in the one suite
    whose install footprint is already the heaviest.
    """
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    return dot / (norm_left * norm_right)


def _write_corpus(root: Path) -> Path:
    corpus = root / "corpus"
    corpus.mkdir()
    for name, text in RAG_DEVICE_CORPUS.items():
        (corpus / name).write_text(text, encoding="utf-8")
    return corpus


def _make_embeddings(device: str | None = None) -> Any:
    from mangomas.adapters.embeddings.sentence_transformers import (  # noqa: PLC0415
        SentenceTransformersEmbeddingClient,
    )

    logger.info("Loading local embedding model", extra={"device": device})
    return SentenceTransformersEmbeddingClient(model=DEFAULT_LOCAL_EMBEDDING_MODEL, device=device)


def _make_store(persist_dir: Path, collection: str) -> Any:
    from mangomas.adapters.vector.chroma import ChromaVectorStore  # noqa: PLC0415

    return ChromaVectorStore(persist_dir=str(persist_dir), collection_name=collection)


async def _ingest(corpus: Path, embeddings: Any, store: Any) -> Any:
    pipeline = IngestionPipeline(
        embeddings=embeddings,
        vector_store=store,
        settings=RagSettings(
            chunk_words=_CHUNK_WORDS,
            chunk_overlap=_CHUNK_OVERLAP,
        ),
        batch_size=_BATCH_SIZE,
    )
    return await pipeline.ingest(str(corpus))


# ── E1: the embedding contract ────────────────────────────────────────────────


async def test_embeddings_are_finite_consistent_and_order_preserving() -> None:
    """Four properties that must hold on every device.

    Bundled into one test because they share an expensive fixture — loading
    the model is the cost here, not the assertions — and because they describe
    one contract: "this backend returns usable vectors in the order asked".
    """
    embeddings = _make_embeddings()
    try:
        texts = list(RAG_DEVICE_CORPUS.values())
        vectors = await embeddings.embed_batch(texts)

        # 1. Every component is a real number. NaN/inf from a broken device
        #    build poisons every downstream similarity silently.
        assert all(math.isfinite(value) for vector in vectors for value in vector)

        # 2. Dimensions agree across rows and with the single-text path.
        single = await embeddings.embed(texts[0])
        assert len({len(v) for v in vectors}) == 1
        assert len(single) == len(vectors[0])

        # 3. Self-similarity is 1 within tolerance — never asserted with `==`,
        #    which is the float32 trap this rule exists for.
        assert _cosine(vectors[0], vectors[0]) == pytest.approx(
            EMBEDDING_SELF_COSINE, abs=EMBEDDING_COSINE_ATOL
        )

        # 4. Batch order is preserved: row i corresponds to text i. A backend
        #    that sorted or re-chunked internally would silently mis-attribute
        #    every stored chunk to the wrong source.
        assert _cosine(vectors[0], single) == pytest.approx(
            EMBEDDING_SELF_COSINE, abs=EMBEDDING_COSINE_ATOL
        )
    finally:
        await embeddings.aclose()


# ── E2: device parity ─────────────────────────────────────────────────────────


async def test_retrieval_ranking_is_identical_on_cpu_and_auto_detect(tmp_path: Path) -> None:
    """The ranking a query produces must not depend on the device.

    On a CPU-only box auto-detect *is* CPU, so this compares a run against
    itself and passes trivially — that is intended: it still exercises the
    forced-device path, and on a GPU box the same test becomes the real
    comparison with no change. Skipping on CPU would mean the assertion only
    ever ran where it could not fail.

    Ranking, not scores: see the module docstring.
    """
    corpus = _write_corpus(tmp_path)
    rankings: dict[str, list[str]] = {}

    for label, device in (("auto", None), ("forced-cpu", DEVICE_CPU)):
        embeddings = _make_embeddings(device)
        store = _make_store(tmp_path / f"chroma-{label}", f"e2e-{label}")
        try:
            report = await _ingest(corpus, embeddings, store)
            assert report.documents == len(RAG_DEVICE_CORPUS)

            retriever = Retriever(
                embeddings=embeddings, vector_store=store, top_k=len(RAG_DEVICE_CORPUS)
            )
            results = await retriever.search(RAG_DEVICE_QUERY)
            rankings[label] = [result.chunk.source for result in results]
            logger.info("Retrieval ranking", extra={"device": label, "order": rankings[label]})
        finally:
            await embeddings.aclose()
            await store.aclose()

    assert rankings["auto"] == rankings["forced-cpu"], (
        f"ranking differs by device: {rankings} — scores may drift, order may not"
    )
    assert rankings["auto"][0].endswith(RAG_DEVICE_EXPECTED_TOP_SOURCE), (
        f"expected {RAG_DEVICE_EXPECTED_TOP_SOURCE} to rank first, got {rankings['auto']}"
    )


async def test_ingest_then_query_round_trip(tmp_path: Path) -> None:
    """The original end-to-end path: ingest a directory, then retrieve from it."""
    corpus = _write_corpus(tmp_path)
    embeddings = _make_embeddings()
    store = _make_store(tmp_path / "chroma", "e2e")
    try:
        report = await _ingest(corpus, embeddings, store)
        assert report.documents == len(RAG_DEVICE_CORPUS)
        assert report.chunks >= len(RAG_DEVICE_CORPUS)

        retriever = Retriever(embeddings=embeddings, vector_store=store, top_k=2)
        results = await retriever.search(RAG_DEVICE_QUERY)

        assert results
        assert results[0].chunk.source.endswith(RAG_DEVICE_EXPECTED_TOP_SOURCE)
        # Scores stay in [0, 1] — ChromaVectorStore maps distance with
        # `1 - d/2` precisely so a caller can rely on that range.
        assert all(0.0 <= result.score <= 1.0 for result in results)
    finally:
        await embeddings.aclose()
        await store.aclose()
