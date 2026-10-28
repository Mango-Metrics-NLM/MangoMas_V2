"""Storage/database provider factories.

Factories for instantiating repository adapters from configuration.
Lazy imports (e.g., PostgreSQL SDK) are deferred so optional extras stay optional.
"""

from __future__ import annotations

import logging
from typing import Any

from mangomas.adapters.storage import SQLiteRepository
from mangomas.adapters.storage._url import dsn_host
from mangomas.config import DBSettings

logger = logging.getLogger(__name__)


def _sqlite_factory(cfg: DBSettings) -> SQLiteRepository:
    """Build a SQLiteRepository from DBSettings.

    Returns an in-process SQLite-backed turn repository suitable for
    single-instance deployments and local development.
    """
    logger.debug("Building SQLiteRepository", extra={"url": cfg.url})
    return SQLiteRepository(cfg.url)


def _postgres_factory(cfg: DBSettings) -> Any:
    """Build a PostgresRepository from DBSettings (asyncpg-backed).

    Returns a PostgreSQL-backed repository suitable for distributed deployments.
    The asyncpg SDK is lazy-imported to keep the optional ``postgres`` extra
    genuinely optional.
    """
    # Host only, never the URL: the DSN carries the password in its userinfo
    # and dev runs at DEBUG with the JSON formatter forwarding every ``extra``
    # field to Cloud Logging (spec-0038; Copilot review on PR #101).
    logger.debug("Building PostgresRepository", extra={"dsn_host": dsn_host(cfg.url)})
    from mangomas.adapters.storage.postgres import PostgresRepository  # noqa: PLC0415

    return PostgresRepository(cfg)


__all__ = [
    "_postgres_factory",
    "_sqlite_factory",
]
