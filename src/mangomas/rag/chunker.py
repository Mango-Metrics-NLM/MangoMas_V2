"""Pure word-window chunker — no I/O, no embeddings, fully deterministic.

Splits text into overlapping windows of at most ``size`` words advancing by
``size - overlap`` words each step. The final window always ends exactly at the
end of the input, so every word is covered by at least one chunk.
"""

from __future__ import annotations

__all__ = ["chunk_text"]


def chunk_text(text: str, *, size: int, overlap: int) -> list[str]:
    """Split ``text`` into overlapping word-windows.

    Args:
        text: Raw document text. Tokenised on whitespace.
        size: Maximum words per chunk. Must be >= 1.
        overlap: Words shared between consecutive chunks. ``0 <= overlap < size``.

    Returns:
        Chunks as whitespace-joined strings, in document order. Empty input
        (no words) yields an empty list.
    """
    if size < 1:
        raise ValueError(f"size must be >= 1, got {size}")
    if overlap < 0 or overlap >= size:
        raise ValueError(f"overlap must satisfy 0 <= overlap < size, got {overlap} (size={size})")

    words = text.split()
    n = len(words)
    if n == 0:
        return []

    step = size - overlap
    chunks: list[str] = []
    start = 0
    while start < n:
        end = min(start + size, n)
        chunks.append(" ".join(words[start:end]))
        if end == n:
            break
        start += step
    return chunks
