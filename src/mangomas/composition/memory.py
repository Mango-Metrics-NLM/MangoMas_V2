"""Memory repository factory.

Factories for instantiating memory adapters from configuration.
"""

from __future__ import annotations

import logging

from mangomas.adapters.storage import FileMemoryRepository
from mangomas.config import MemorySettings

logger = logging.getLogger(__name__)


def _file_memory_factory(cfg: MemorySettings) -> FileMemoryRepository:
    """Build a FileMemoryRepository from MemorySettings.

    Returns a file-backed memory repository for persisting agent memory
    across sessions.
    """
    logger.debug(
        "Building FileMemoryRepository",
        extra={"memory_dir": cfg.memory_dir, "index_file": cfg.index_file},
    )
    return FileMemoryRepository(cfg)


__all__ = [
    "_file_memory_factory",
]
