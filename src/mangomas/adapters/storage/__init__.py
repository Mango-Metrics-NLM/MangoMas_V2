"""Storage adapters."""

from __future__ import annotations

from mangomas.adapters.storage.base import (
    AsyncCloseableRepository,
    FailureRecordingRepository,
    MemoryRepository,
    TurnRepository,
)
from mangomas.adapters.storage.memory import FileMemoryRepository
from mangomas.adapters.storage.postgres import PostgresRepository
from mangomas.adapters.storage.sqlite import SQLiteRepository

__all__ = [
    "AsyncCloseableRepository",
    "FailureRecordingRepository",
    "FileMemoryRepository",
    "MemoryRepository",
    "PostgresRepository",
    "SQLiteRepository",
    "TurnRepository",
]
