"""Clock utility for deterministic time."""

from __future__ import annotations

from datetime import UTC, datetime


def now() -> datetime:
    """Return the current UTC time.

    Mock this function in tests to freeze time.
    """
    return datetime.now(UTC)
