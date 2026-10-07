"""Pure word-window chunker — no I/O, no embeddings, fully deterministic.

Splits text into overlapping windows of at most ``size`` words advancing by
``size - overlap`` words each step. The final window always ends exactly at the
end of the input, so every word is covered by at least one chunk.
"""

from __future__ import annotations

import re

__all__ = ["chunk_lines", "chunk_text"]

_WORD = re.compile(r"\S+")


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


def _line_units(text: str, size: int) -> list[tuple[str, int]]:
    """Split *text* into ``(slice, word_count)`` units that never cross a line.

    A line that fits within *size* words is one unit, newline included, so a
    Markdown table row is never cut in half. A longer line is cut at word
    boundaries into slices of at most *size* words. Slicing the original string
    (rather than re-joining words) keeps indentation and spacing intact.
    """
    units: list[tuple[str, int]] = []
    for line in text.splitlines(keepends=True):
        words = list(_WORD.finditer(line))
        if len(words) <= size:
            units.append((line, len(words)))
            continue
        start = 0
        for first in range(0, len(words), size):
            last = words[min(first + size, len(words)) - 1]
            end = last.end() if first + size < len(words) else len(line)
            units.append((line[start:end], min(size, len(words) - first)))
            start = end
    return units


def chunk_lines(text: str, *, size: int, overlap: int) -> list[str]:
    """Split parsed Markdown into overlapping windows of whole lines (spec-0035 R7).

    Unlike :func:`chunk_text`, which re-joins words with single spaces and so
    flattens every newline, each chunk here is a contiguous slice of *text*:
    line breaks, indentation and table rows survive. Windows pack whole lines
    up to *size* words; a single line longer than *size* is cut at word
    boundaries. Consecutive windows share trailing lines totalling at most
    *overlap* words. Every word of *text* appears in at least one chunk, and a
    text with no words yields ``[]`` (matching :func:`chunk_text`).

    Args:
        text: Parsed document text (typically Markdown).
        size: Maximum words per chunk. Must be >= 1.
        overlap: Maximum words shared between consecutive chunks. ``0 <= overlap < size``.
    """
    if size < 1:
        raise ValueError(f"size must be >= 1, got {size}")
    if overlap < 0 or overlap >= size:
        raise ValueError(f"overlap must satisfy 0 <= overlap < size, got {overlap} (size={size})")
    units = _line_units(text, size)
    chunks: list[str] = []
    start = 0
    while start < len(units):
        end = start
        words = 0
        while end < len(units) and (end == start or words + units[end][1] <= size):
            words += units[end][1]
            end += 1
        if words:
            chunks.append("".join(unit for unit, _ in units[start:end]))
        if end >= len(units):
            break
        # Step back over trailing units that fit in the overlap budget, but
        # always advance at least one unit so the loop terminates.
        back = end
        shared = 0
        while back - 1 > start and shared + units[back - 1][1] <= overlap:
            back -= 1
            shared += units[back][1]
        start = back
    return chunks
