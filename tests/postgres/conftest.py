"""Shared fixtures for the Postgres testcontainers suite.

All tests in this directory are gated by ``RUN_POSTGRES=1`` via the
project's ``pytest_collection_modifyitems`` hook. They spawn an ephemeral
``postgres:16-alpine`` container per session, and a fresh
:class:`PostgresRepository` per test (recreating the schema lives in the
repo's lazy ``_ensure_pool`` path, so no per-test teardown of tables is
required).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest

from mangomas.adapters.storage.postgres import PostgresRepository
from mangomas.config import DBSettings
from tests.constants import (
    POSTGRES_TEST_DB,
    POSTGRES_TEST_IMAGE,
    POSTGRES_TEST_PASSWORD,
    POSTGRES_TEST_USER,
)


@pytest.fixture(scope="session")
def postgres_dsn() -> Iterator[str]:
    """Spin up a session-scoped Postgres container and yield its DSN."""
    from testcontainers.postgres import PostgresContainer  # noqa: PLC0415  optional dev dep

    container = PostgresContainer(
        POSTGRES_TEST_IMAGE,
        username=POSTGRES_TEST_USER,
        password=POSTGRES_TEST_PASSWORD,
        dbname=POSTGRES_TEST_DB,
    )
    container.start()
    try:
        # testcontainers returns a SQLAlchemy-style URL; coerce to asyncpg's
        # canonical scheme (PostgresRepository also normalises legacy schemes,
        # so this is belt-and-braces).
        raw = container.get_connection_url()
        # Strip any "+psycopg" / "+psycopg2" driver suffix from the scheme.
        if "+" in raw.split("://", 1)[0]:
            scheme, rest = raw.split("://", 1)
            raw = f"{scheme.split('+', 1)[0]}://{rest}"
        yield raw
    finally:
        container.stop()


@pytest.fixture
async def postgres_repo(postgres_dsn: str) -> AsyncIterator[PostgresRepository]:
    """Yield a freshly-pooled :class:`PostgresRepository` and tear it down."""
    cfg = DBSettings(
        provider="postgres",
        url=postgres_dsn,
        pool_min=1,
        pool_max=10,
        connect_timeout_seconds=10.0,
    )
    repo = PostgresRepository(cfg)
    try:
        yield repo
    finally:
        await repo.aclose()
