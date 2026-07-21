"""SQLite results sink — persist an :class:`EvalReport` (and its rows) to SQLite.

Reuses the storage-adapter idiom (stdlib ``sqlite3`` + ``CREATE TABLE IF NOT
EXISTS`` + writes wrapped in ``asyncio.to_thread``). Each ``emit`` opens its own
connection, writes the report and rows in one transaction, and closes it — so
the sink holds no long-lived handle and stays safe under the sequential,
fault-isolated emission the CLI performs. The report is the canonical baseline
artifact for regression gating; this sink is a queryable secondary store.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mangomas.errors import ConfigError, PersistenceError
from mangomas.eval.sink import Sink
from mangomas.eval.sink_registry import sink_registry

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.eval.gate import GateResult
    from mangomas.eval.runner import EvalReport

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS eval_reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT    NOT NULL,
    scorer       TEXT    NOT NULL,
    agent_name   TEXT    NOT NULL,
    target_name  TEXT    NOT NULL,
    dataset_size INTEGER NOT NULL,
    passed       INTEGER NOT NULL,
    failed       INTEGER NOT NULL,
    errored      INTEGER NOT NULL,
    mean_score   REAL    NOT NULL,
    duration_ms  REAL    NOT NULL,
    gate_json    TEXT
);
CREATE TABLE IF NOT EXISTS eval_rows (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id     INTEGER NOT NULL,
    row_id        TEXT    NOT NULL,
    score         REAL    NOT NULL,
    passed        INTEGER NOT NULL,
    duration_ms   REAL    NOT NULL,
    prediction    TEXT    NOT NULL,
    expected      TEXT    NOT NULL,
    error         TEXT,
    metadata_json TEXT,
    FOREIGN KEY (report_id) REFERENCES eval_reports(id)
);
"""


class SqliteResultsSink:
    """Append the report + its rows to two tables in a SQLite database."""

    name = "sqlite_results"

    def __init__(self, *, db_path: str) -> None:
        self._path = db_path

    async def emit(
        self,
        report: EvalReport,
        *,
        gate_result: GateResult | None = None,
    ) -> None:
        await asyncio.to_thread(self._write, report, gate_result)
        logger.debug(
            "Eval report stored in SQLite",
            extra={"event": "eval_sink_sqlite_results", "db_path": self._path},
        )

    def _write(self, report: EvalReport, gate_result: GateResult | None) -> None:
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        gate_json = json.dumps(dataclasses.asdict(gate_result)) if gate_result is not None else None
        conn = sqlite3.connect(self._path)
        try:
            conn.executescript(_SCHEMA)
            cur = conn.execute(
                "INSERT INTO eval_reports "
                "(ts, scorer, agent_name, target_name, dataset_size, passed, failed, "
                "errored, mean_score, duration_ms, gate_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(UTC).isoformat(),
                    report.scorer,
                    report.agent_name,
                    report.target_name,
                    report.dataset_size,
                    report.passed,
                    report.failed,
                    report.errored,
                    report.mean_score,
                    report.duration_ms,
                    gate_json,
                ),
            )
            report_id = int(cur.lastrowid or 0)
            conn.executemany(
                "INSERT INTO eval_rows "
                "(report_id, row_id, score, passed, duration_ms, prediction, expected, "
                "error, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        report_id,
                        row.row_id,
                        row.score,
                        int(row.passed),
                        row.duration_ms,
                        row.prediction,
                        row.expected,
                        row.error,
                        json.dumps(row.metadata),
                    )
                    for row in report.rows
                ],
            )
            conn.commit()
        except sqlite3.Error as exc:
            raise PersistenceError(str(exc)) from exc
        finally:
            conn.close()


def _sqlite_results_factory(options: dict[str, Any]) -> Sink:
    db_path = options.get("db_path")
    if not db_path or not isinstance(db_path, str):
        raise ConfigError("sqlite_results sink requires a string 'db_path' option")
    return SqliteResultsSink(db_path=db_path)


sink_registry.register("sqlite_results", _sqlite_results_factory)
