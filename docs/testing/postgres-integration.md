# Postgres Integration Test Plan

This document describes how to exercise `PostgresRepository` end-to-end
against a real Postgres instance, both in CI (via `testcontainers-python`)
and locally (via `docker compose --profile postgres`).

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `RUN_POSTGRES` | _(unset)_ | Set to `1` to enable `tests/postgres/` collection |
| `MANGOMAS_DB__PROVIDER` | `sqlite` | Set to `postgres` to activate the adapter |
| `MANGOMAS_DB__URL` | `sqlite:///./data/mangomas.db` | asyncpg DSN, e.g. `postgresql://user:pass@host:5432/mangomas` |
| `MANGOMAS_DB__POOL_MIN` | `1` | asyncpg pool minimum |
| `MANGOMAS_DB__POOL_MAX` | `10` | asyncpg pool maximum |
| `MANGOMAS_DB__CONNECT_TIMEOUT_SECONDS` | `10.0` | asyncpg connect timeout |
| `MANGOMAS_DB__STATEMENT_TIMEOUT_SECONDS` | _(unset)_ | Per-statement timeout in seconds |

## Confidence gate before running any scenario

1. Full local quality gate passes (`ruff`, `format`, `mypy`, `pytest`,
   `check_coverage.py`).
2. Docker is running (`docker info` returns a daemon).
3. Either:
   - `pip install -e ".[dev,postgres]"` (testcontainers path), OR
   - `docker compose --profile postgres up -d postgres` (compose path).

## Scenarios

### Scenario 1 — testcontainers smoke (`tests/postgres/test_postgres_smoke.py`)

`save_turn` + `list_turns` round-trip preserves the JSON shape (request
and response decode back to `dict`, matching `SQLiteRepository`
behaviour). `aclose()` is idempotent.

```
RUN_POSTGRES=1 python -m pytest tests/postgres/test_postgres_smoke.py -q --no-cov
```

### Scenario 2 — persistence + ordering (`tests/postgres/test_persistence.py`)

Five inserts; `list_turns` returns newest first; `limit` honoured; the
lazy `_ensure_pool` is idempotent (second call returns the same pool).

```
RUN_POSTGRES=1 python -m pytest tests/postgres/test_persistence.py -q --no-cov
```

### Scenario 3 — fan-out concurrency (`tests/postgres/test_concurrency.py`)

50 parallel `save_turn` calls via `asyncio.gather`; asserts all rows
persist with unique ids. Pins the asyncpg pool's concurrent-write
contract — mirrors the SQLite regression test added in v0.3.0
(`tests/test_sqlite_concurrency.py`).

```
RUN_POSTGRES=1 python -m pytest tests/postgres/test_concurrency.py -q --no-cov
```

### Scenario 4 — manual compose smoke (CLI through the adapter)

```
docker compose --profile postgres up -d postgres
MANGOMAS_DB__PROVIDER=postgres \
  MANGOMAS_DB__URL=postgresql://mangomas:mangomas_local@localhost:5432/mangomas \
  mangomas chat "hello"
docker compose --profile postgres down -v
```

## Implementation notes

- All tests in `tests/postgres/` are marked `@pytest.mark.postgres` (via
  `pytestmark` at module scope) and live under the path component
  `postgres`, so the project's `pytest_collection_modifyitems` hook skips
  them unless `RUN_POSTGRES=1`.
- `asyncio_mode = "auto"` is already set — do not add `@pytest.mark.asyncio`.
- The testcontainers `PostgresContainer` is session-scoped; the
  `PostgresRepository` fixture is function-scoped and tears down the pool
  via `aclose()` on every test.
- No Alembic migration tool — schema is created on first connect via
  `CREATE TABLE IF NOT EXISTS turns`, matching the SQLite pattern.
