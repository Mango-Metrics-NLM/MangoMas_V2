"""Pure word-window chunker — no I/O, no embeddings, fully deterministic.

Splits text into overlapping windows of at most ``size`` words advancing by
``size - overlap`` words each step. The final window always ends exactly at the
end of the input, so every word is covered by at least one chunk. A trailing
fragment shorter than ``min_words`` is dropped only when the preceding chunk's
overlap region already covers it, so coverage is never lost.
"""

from __future__ import annotations

__all__ = ["chunk_text"]


def chunk_text(text: str, *, size: int, overlap: int, min_words: int) -> list[str]:
    """Split ``text`` into overlapping word-windows.

    Args:
        text: Raw document text. Tokenised on whitespace.
        size: Maximum words per chunk. Must be >= 1.
        overlap: Words shared between consecutive chunks. ``0 <= overlap < size``.
        min_words: Trailing fragments shorter than this are dropped when already
            covered by the previous chunk's overlap. ``>= 0``.

    Returns:
        Chunks as whitespace-joined strings, in document order. Empty input
        (no words) yields an empty list.
    """
    if size < 1:
        raise ValueError(f"size must be >= 1, got {size}")
    if overlap < 0 or overlap >= size:
        raise ValueError(f"overlap must satisfy 0 <= overlap < size, got {overlap} (size={size})")
    if min_words < 0:
        raise ValueError(f"min_words must be >= 0, got {min_words}")

    words = text.split()
    n = len(words)
    if n == 0:
        return []

    step = size - overlap
    chunks: list[str] = []
    start = 0
    while start < n:
        end = min(start + size, n)
        window = words[start:end]
        is_last = end == n
        too_short = len(window) < min_words
        covered_by_prev = bool(chunks) and (n - start) <= overlap
        if is_last and too_short and covered_by_prev:
            break
        chunks.append(" ".join(window))
        if is_last:
            break
        start += step
    return chunks
