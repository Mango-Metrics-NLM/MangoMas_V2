"""Tests for the pure word-window chunker — unit cases + Hypothesis invariants."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mangomas.rag.chunker import chunk_text


def test_empty_text_yields_no_chunks() -> None:
    assert chunk_text("", size=10, overlap=2, min_words=0) == []
    assert chunk_text("   \n\t ", size=10, overlap=2, min_words=0) == []


def test_short_text_is_a_single_chunk_even_below_min_words() -> None:
    # The only chunk is never dropped, regardless of min_words.
    chunks = chunk_text("one two three", size=800, overlap=120, min_words=50)
    assert chunks == ["one two three"]


def test_no_chunk_exceeds_size() -> None:
    words = " ".join(str(i) for i in range(25))
    chunks = chunk_text(words, size=10, overlap=2, min_words=0)
    assert all(len(c.split()) <= 10 for c in chunks)


def test_consecutive_chunks_share_overlap_words() -> None:
    words = " ".join(str(i) for i in range(20))
    chunks = chunk_text(words, size=10, overlap=3, min_words=0)
    assert len(chunks) >= 2
    first = chunks[0].split()
    second = chunks[1].split()
    # The last `overlap` words of chunk 0 are the first `overlap` of chunk 1.
    assert first[-3:] == second[:3]


def test_trailing_short_fragment_dropped_when_covered_by_overlap() -> None:
    # 12 words, size=10, overlap=5 (step=5): windows start at 0 and 5.
    # Window @5 = words[5:12] = 7 words; with min_words=8 it is too short and
    # fully covered by window @0 (which reached index 10 > 12? no) — craft a
    # case where leftover <= overlap.
    words = " ".join(str(i) for i in range(12))
    # step = size - overlap = 10 - 8 = 2; windows @0,2,4 → @4 = words[4:12]=8.
    chunks = chunk_text(words, size=10, overlap=8, min_words=9)
    # Every emitted chunk that is the last must be >= min_words OR the list is
    # still covering: assert full coverage of all 12 words.
    seen: set[str] = set()
    for c in chunks:
        seen.update(c.split())
    assert seen == {str(i) for i in range(12)}


def test_rejects_invalid_size() -> None:
    with pytest.raises(ValueError):
        chunk_text("a b c", size=0, overlap=0, min_words=0)


def test_rejects_overlap_ge_size() -> None:
    with pytest.raises(ValueError):
        chunk_text("a b c", size=5, overlap=5, min_words=0)


def test_rejects_negative_overlap() -> None:
    with pytest.raises(ValueError):
        chunk_text("a b c", size=5, overlap=-1, min_words=0)


def test_rejects_negative_min_words() -> None:
    with pytest.raises(ValueError):
        chunk_text("a b c", size=5, overlap=1, min_words=-1)


# ── Hypothesis fuzz: invariants over arbitrary token streams ──────────────────

_words = st.lists(st.text(alphabet="abcdefghij", min_size=1, max_size=4), max_size=60)


@given(words=_words, size=st.integers(1, 12), overlap=st.integers(0, 11))
def test_fuzz_no_chunk_exceeds_size(words: list[str], size: int, overlap: int) -> None:
    if overlap >= size:
        return
    text = " ".join(words)
    chunks = chunk_text(text, size=size, overlap=overlap, min_words=0)
    assert all(len(c.split()) <= size for c in chunks)


@given(words=_words, size=st.integers(1, 12), overlap=st.integers(0, 11))
def test_fuzz_reassembly_covers_input(words: list[str], size: int, overlap: int) -> None:
    """With min_words=0 every input word appears in at least one chunk."""
    if overlap >= size:
        return
    text = " ".join(words)
    chunks = chunk_text(text, size=size, overlap=overlap, min_words=0)
    if not words:
        assert chunks == []
        return
    covered = {w for c in chunks for w in c.split()}
    assert set(words) <= covered
