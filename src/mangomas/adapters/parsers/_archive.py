"""OOXML zip archive validation and decompression ceiling guards (spec-0035 R4 / ADR-0036).

Pure in-memory checks run before any network upload to reject zip bombs, excessive
directory entries, or non-zip payloads for OOXML containers (.docx, .pptx, .xlsx).
"""

from __future__ import annotations

import io
import zipfile
from typing import Final

from mangomas.errors import DocumentParseError

__all__ = ["OOXML_SUFFIXES", "check_ooxml_archive"]

# Zip-container formats that get the archive pre-check.
OOXML_SUFFIXES: Final[frozenset[str]] = frozenset({".docx", ".pptx", ".xlsx"})

# Archive errors zipfile raises for a corrupt or non-zip body.
_ZIP_ERRORS: Final[tuple[type[Exception], ...]] = (
    zipfile.BadZipFile,
    zipfile.LargeZipFile,
    ValueError,
    OSError,
    EOFError,
    NotImplementedError,
)


def check_ooxml_archive(content: bytes, *, max_entries: int, max_ratio: float) -> None:
    """Refuse an OOXML body that is not a zip or that would expand suspiciously.

    Pure (in-memory ``BytesIO``; no I/O). Rejects a body that is not a readable
    zip, one with more than ``max_entries`` members, and one whose total
    declared uncompressed size exceeds ``max_ratio`` times its total compressed
    size — the zip-bomb shape. Uses the sizes the central directory declares,
    which is what a downstream reader trusts too; it does not decompress.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
    except _ZIP_ERRORS as exc:
        raise DocumentParseError(
            "document is not a readable OOXML archive", detail=type(exc).__name__
        ) from None
    if len(entries) > max_entries:
        raise DocumentParseError(
            "OOXML archive has too many entries",
            detail=f"entries={len(entries)} max_entries={max_entries}",
        )
    uncompressed = sum(info.file_size for info in entries)
    compressed = sum(info.compress_size for info in entries)
    # Multiplication, not division: zero compressed bytes that expand to
    # anything is an unbounded ratio and must be refused, not a ZeroDivisionError.
    if uncompressed > max_ratio * compressed:
        raise DocumentParseError(
            "OOXML archive compression ratio exceeds the limit",
            detail=f"uncompressed={uncompressed} compressed={compressed} max_ratio={max_ratio}",
        )
