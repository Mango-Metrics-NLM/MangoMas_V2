"""Turn-storage and file-memory settings (`MANGOMAS_DB__*`, `MANGOMAS_MEMORY__*`)."""

from __future__ import annotations

from pydantic import BaseModel

DEFAULT_DB_PROVIDER: str = "sqlite"


DEFAULT_DB_URL: str = "sqlite:///./data/mangomas.db"


# asyncpg pool + connection knobs — consumed when MANGOMAS_DB__PROVIDER=postgres.
DEFAULT_DB_POOL_MIN: int = 1


DEFAULT_DB_POOL_MAX: int = 10


DEFAULT_DB_CONNECT_TIMEOUT_SECONDS: float = 10.0


DEFAULT_DB_STATEMENT_TIMEOUT_SECONDS: float | None = None


# Default page size for ``TurnRepository.list_turns``. Lives here rather than
# as a bare literal in the Protocol and each backend: the same default was
# written three times (base/sqlite/postgres), so an implementation could
# silently disagree with the Protocol it claims to satisfy.
DEFAULT_STORAGE_LIST_TURNS_LIMIT: int = 50


class DBSettings(BaseModel):
    """Persistence configuration."""

    provider: str = DEFAULT_DB_PROVIDER
    url: str = DEFAULT_DB_URL
    # asyncpg pool + connection knobs — used only by the postgres provider.
    pool_min: int = DEFAULT_DB_POOL_MIN
    pool_max: int = DEFAULT_DB_POOL_MAX
    connect_timeout_seconds: float = DEFAULT_DB_CONNECT_TIMEOUT_SECONDS
    statement_timeout_seconds: float | None = DEFAULT_DB_STATEMENT_TIMEOUT_SECONDS


DEFAULT_MEMORY_PROVIDER: str = "file"


DEFAULT_MEMORY_DIR: str = "memory"


DEFAULT_MEMORY_INDEX: str = "MEMORY.md"


DEFAULT_MEMORY_ENABLED: bool = False


class MemorySettings(BaseModel):
    """Dual-layer markdown memory configuration."""

    enabled: bool = DEFAULT_MEMORY_ENABLED
    provider: str = DEFAULT_MEMORY_PROVIDER
    memory_dir: str = DEFAULT_MEMORY_DIR
    index_file: str = DEFAULT_MEMORY_INDEX
