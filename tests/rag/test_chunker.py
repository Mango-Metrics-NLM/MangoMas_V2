"""Tests for the pure word-window chunker — unit cases + Hypothesis invariants."""

from __future__ import annotations

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from mangomas.rag.chunker import chunk_text


def test_empty_text_yields_no_chunks() -> None:
    assert chunk_text("", size=10, overlap=2) == []
    assert chunk_text("   \n\t ", size=10, overlap=2) == []


def test_short_text_is_a_single_chunk() -> None:
    chunks = chunk_text("one two three", size=800, overlap=120)
    assert chunks == ["one two three"]


def test_no_chunk_exceeds_size() -> None:
    words = " ".join(str(i) for i in range(25))
    chunks = chunk_text(words, size=10, overlap=2)
    assert all(len(c.split()) <= 10 for c in chunks)


def test_consecutive_chunks_share_overlap_words() -> None:
    words = " ".join(str(i) for i in range(20))
    chunks = chunk_text(words, size=10, overlap=3)
    assert len(chunks) >= 2
    first = chunks[0].split()
    second = chunks[1].split()
    # The last `overlap` words of chunk 0 are the first `overlap` of chunk 1.
    assert first[-3:] == second[:3]


def test_final_window_covers_the_tail() -> None:
    """A short last window is kept, not dropped — every input word is covered."""
    words = " ".join(str(i) for i in range(12))
    chunks = chunk_text(words, size=10, overlap=8)
    seen: set[str] = set()
    for c in chunks:
        seen.update(c.split())
    assert seen == {str(i) for i in range(12)}


def test_rejects_invalid_size() -> None:
    with pytest.raises(ValueError):
        chunk_text("a b c", size=0, overlap=0)


def test_rejects_overlap_ge_size() -> None:
    with pytest.raises(ValueError):
        chunk_text("a b c", size=5, overlap=5)


def test_rejects_negative_overlap() -> None:
    with pytest.raises(ValueError):
        chunk_text("a b c", size=5, overlap=-1)


# ── Hypothesis fuzz: invariants over arbitrary token streams ──────────────────

_words = st.lists(st.text(alphabet="abcdefghij", min_size=1, max_size=4), max_size=60)


@given(
    words=_words,
    size=st.integers(1, 12),
    overlap=st.integers(0, 11),
)
def test_fuzz_no_chunk_exceeds_size(words: list[str], size: int, overlap: int) -> None:
    assume(overlap < size)
    text = " ".join(words)
    chunks = chunk_text(text, size=size, overlap=overlap)
    assert all(len(c.split()) <= size for c in chunks)


@given(
    words=_words,
    size=st.integers(1, 12),
    overlap=st.integers(0, 11),
)
def test_fuzz_reassembly_covers_input(words: list[str], size: int, overlap: int) -> None:
    """Every input word is covered by at least one emitted chunk."""
    assume(overlap < size)
    text = " ".join(words)
    chunks = chunk_text(text, size=size, overlap=overlap)
    if not words:
        assert chunks == []
        return
    covered = {w for c in chunks for w in c.split()}
    assert set(words) <= covered


# ── chunk_lines (spec-0035 R7: line-preserving windows for parsed documents) ──

from mangomas.rag.chunker import chunk_lines  # noqa: E402

_TABLE = "| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n"


def test_chunk_lines_keeps_leading_whitespace_and_indentation() -> None:
    """Regression (PR #82 review): a `\\S+\\s*` tokenizer dropped leading space."""
    text = "    indented code\n  more code\n"
    assert chunk_lines(text, size=50, overlap=0) == [text]


def test_chunk_lines_keeps_a_table_intact_when_it_fits() -> None:
    assert chunk_lines(_TABLE, size=50, overlap=0) == [_TABLE]


def test_chunk_lines_never_splits_a_row_that_fits_the_window() -> None:
    for chunk in chunk_lines(_TABLE, size=6, overlap=0):
        for line in chunk.splitlines():
            assert line in _TABLE.splitlines()


def test_chunk_lines_splits_an_over_long_line_at_word_boundaries() -> None:
    assert chunk_lines("a b c d e", size=2, overlap=0) == ["a b", " c d", " e"]


def test_chunk_lines_of_wordless_text_is_empty() -> None:
    assert chunk_lines("  \n\n\t", size=3, overlap=0) == []


def test_chunk_lines_overlap_repeats_trailing_lines() -> None:
    chunks = chunk_lines("one two\nthree four\nfive six\n", size=4, overlap=2)
    assert chunks == ["one two\nthree four\n", "three four\nfive six\n"]


@pytest.mark.parametrize(("size", "overlap"), [(0, 0), (3, 3), (3, -1)])
def test_chunk_lines_validates_its_window(size: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        chunk_lines("a b c", size=size, overlap=overlap)


_LINE = st.text(alphabet=" abc|-\t", max_size=20)


@given(
    lines=st.lists(_LINE, max_size=12),
    size=st.integers(min_value=1, max_value=8),
    data=st.data(),
)
def test_chunk_lines_properties(lines: list[str], size: int, data: st.DataObject) -> None:
    text = "\n".join(lines)
    overlap = data.draw(st.integers(min_value=0, max_value=size - 1))
    chunks = chunk_lines(text, size=size, overlap=overlap)
    # Every chunk is a verbatim slice of the input and within the word budget.
    for chunk in chunks:
        assert chunk in text
        assert 1 <= len(chunk.split()) <= size
    # Every word survives: the chunks, overlaps removed, cover every word.
    assert set(text.split()) <= {w for chunk in chunks for w in chunk.split()}
    assert sum(len(c.split()) for c in chunks) >= len(text.split())


def test_chunk_lines_preserves_crlf_and_lone_cr() -> None:
    text = "| a |\r\n|---|\r\n| 1 |\rend\n"
    assert chunk_lines(text, size=50, overlap=0) == [text]
