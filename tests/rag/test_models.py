"""Tests for the RAG domain models (frozen value objects)."""

from __future__ import annotations

import dataclasses

import pytest

from mangomas.rag.models import Chunk, SearchResult


def test_chunk_defaults_empty_metadata() -> None:
    chunk = Chunk(id="doc#0", text="hello", source="doc", index=0)
    assert chunk.metadata == {}


def test_chunk_is_frozen() -> None:
    chunk = Chunk(id="doc#0", text="hello", source="doc", index=0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        chunk.text = "mutated"  # type: ignore[misc]


def test_search_result_pairs_chunk_and_score() -> None:
    chunk = Chunk(id="doc#1", text="world", source="doc", index=1, metadata={"source": "doc"})
    result = SearchResult(chunk=chunk, score=0.87)
    assert result.chunk is chunk
    assert result.score == 0.87


def test_search_result_is_frozen() -> None:
    chunk = Chunk(id="doc#0", text="x", source="doc", index=0)
    result = SearchResult(chunk=chunk, score=0.5)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.score = 0.9  # type: ignore[misc]
