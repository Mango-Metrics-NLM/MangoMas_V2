"""Embedding client protocol — the only surface other modules depend on.

Mirrors :mod:`mangomas.adapters.llm.base`: a single ``@runtime_checkable``
Protocol that every embedding backend (LM Studio, sentence-transformers,
Vertex) satisfies. ``embed`` returns one vector; ``embed_batch`` returns one
vector per input text (and is what the ingestion pipeline drives). Vectors are
plain ``list[float]`` — no numpy — matching the ``_cosine_similarity`` style in
:mod:`mangomas.eval.scorers.embedding`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingClient(Protocol):
    """Minimal async text-embedding contract."""

    async def embed(self, text: str) -> list[float]:
        """Return the embedding vector for a single ``text``."""
        ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per text, in input order."""
        ...

    async def aclose(self) -> None:
        """Release underlying resources."""
        ...
