"""Backend-agnostic turn-record shape, shared by every storage adapter.

One definition of the record, two backends. The alternative — each adapter
spelling its own columns — is how a SQLite row and a Postgres row come to mean
subtly different things, which is exactly the drift the governance audit found
between two hand-copied governance modules.

The migration helper here is *additive only*: a column is appended when absent
and never dropped, altered or reordered, so a database written by an older
build keeps working and its historical rows adopt the column default. That is
the same contract ``tenant`` shipped under (spec 0007 / ADR-0017); this
generalises it rather than adding a third hand-rolled copy of it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

# Bumped whenever a column is added. Stamped on every row written, so a
# consumer can tell a record written by this build from one written before the
# column it is looking for existed — the thing an unversioned record can never
# answer (governance audit §6).
TURN_SCHEMA_VERSION: Final[int] = 2


class TurnStatus(StrEnum):
    """Terminal outcome of one dispatch.

    A ``StrEnum`` so it compares equal to the plain string a database driver
    hands back, and so a caller cannot invent a third status by typo.
    """

    OK = "ok"
    ERROR = "error"


@dataclass(frozen=True)
class TurnColumn:
    """One additive column, in both dialects.

    ``sqlite_type`` and ``postgres_type`` carry the full type-plus-default
    fragment rather than just a type name, because the default is part of what
    makes the migration safe for existing rows and must not drift between
    backends.
    """

    name: str
    sqlite_type: str
    postgres_type: str


# Ordered as they are appended to an existing table. Names are shared with the
# dict keys ``list_turns`` returns, so a consumer reads one vocabulary.
TURN_RECORD_COLUMNS: Final[tuple[TurnColumn, ...]] = (
    TurnColumn(
        name="schema_version",
        sqlite_type=f"INTEGER NOT NULL DEFAULT {TURN_SCHEMA_VERSION}",
        postgres_type=f"INTEGER NOT NULL DEFAULT {TURN_SCHEMA_VERSION}",
    ),
    TurnColumn(
        # Pre-existing rows default to ``ok`` because that is what they were:
        # before this column existed, a row was only ever written on success.
        name="status",
        sqlite_type=f"TEXT NOT NULL DEFAULT '{TurnStatus.OK}'",
        postgres_type=f"TEXT NOT NULL DEFAULT '{TurnStatus.OK}'",
    ),
    TurnColumn(
        # The typed ``MangomasError.code``, so failures are groupable without
        # parsing prose.
        name="error_code",
        sqlite_type="TEXT",
        postgres_type="TEXT",
    ),
    TurnColumn(name="error", sqlite_type="TEXT", postgres_type="TEXT"),
)

# Columns every backend selects, in order, for ``list_turns``. Built from the
# tuple above so adding a column cannot leave one backend's SELECT behind.
_BASE_SELECT_COLUMNS: Final[tuple[str, ...]] = ("id", "ts", "agent", "request", "response")
TURN_SELECT_COLUMNS: Final[tuple[str, ...]] = _BASE_SELECT_COLUMNS + tuple(
    column.name for column in TURN_RECORD_COLUMNS
)


# Columns stored as JSON text and handed back decoded.
_JSON_COLUMNS: Final[frozenset[str]] = frozenset({"request", "response"})


def turn_row_to_dict(row: Sequence[object]) -> dict[str, object]:
    """Map a :data:`TURN_SELECT_COLUMNS`-ordered row to its public dict shape.

    ``strict=True`` is the point: a hand-indexed mapper (``row[0]``, ``row[1]``,
    ...) silently keeps working when a column is added to the SELECT and simply
    stops returning the new one. Zipping strictly against the shared column
    tuple turns that same mistake into a loud ``ValueError`` at the first read.
    """
    record: dict[str, object] = dict(zip(TURN_SELECT_COLUMNS, row, strict=True))
    for key in _JSON_COLUMNS:
        raw = record[key]
        record[key] = json.loads(raw) if isinstance(raw, str) else raw
    return record


def sqlite_column_ddl(column: TurnColumn) -> str:
    """Return the ``ALTER TABLE ... ADD COLUMN`` fragment for SQLite."""
    return f"{column.name} {column.sqlite_type}"


def postgres_column_ddl(column: TurnColumn) -> str:
    """Return the ``ALTER TABLE ... ADD COLUMN`` fragment for PostgreSQL."""
    return f"{column.name} {column.postgres_type}"


__all__ = [
    "TURN_RECORD_COLUMNS",
    "TURN_SCHEMA_VERSION",
    "TURN_SELECT_COLUMNS",
    "TurnColumn",
    "TurnStatus",
    "postgres_column_ddl",
    "sqlite_column_ddl",
    "turn_row_to_dict",
]
