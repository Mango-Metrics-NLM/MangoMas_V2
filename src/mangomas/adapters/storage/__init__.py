"""Storage adapters."""

from mangomas.adapters.storage.base import (
    AsyncCloseableRepository,
    MemoryRepository,
    TurnRepository,
)
from mangomas.adapters.storage.memory import FileMemoryRepository
from mangomas.adapters.storage.sqlite import SQLiteRepository

__all__ = [
    "AsyncCloseableRepository",
    "FileMemoryRepository",
    "MemoryRepository",
    "SQLiteRepository",
    "TurnRepository",
]
