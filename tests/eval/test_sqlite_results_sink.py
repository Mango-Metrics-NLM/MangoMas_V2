"""Tests for the SQLite eval-results sink."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import mangomas.eval.sinks  # noqa: F401 — registers built-in sinks
from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE
from mangomas.errors import ConfigError, PersistenceError
from mangomas.eval import evaluate_gate
from mangomas.eval.runner import EvalReport, EvalRowResult
from mangomas.eval.sink_registry import sink_registry
from mangomas.eval.sinks.sqlite_results import SqliteResultsSink
from tests.constants import EVAL_COST_USD_METADATA_KEY


def _report() -> EvalReport:
    return EvalReport(
        scorer="exact_match",
        agent_name="chat",
        dataset_size=2,
        passed=1,
        failed=1,
        errored=0,
        mean_score=0.5,
        duration_ms=12.0,
        rows=[
            EvalRowResult(
                row_id="r1",
                score=1.0,
                passed=True,
                duration_ms=1.0,
                prediction="a",
                expected="a",
                metadata={"k": "v"},
            ),
            EvalRowResult(
                row_id="r2",
                score=0.0,
                passed=False,
                duration_ms=2.0,
                prediction="b",
                expected="c",
                error="boom",
            ),
        ],
        target_name="chat",
    )


async def test_sqlite_results_sink_writes_report_and_rows(tmp_path: Path) -> None:
    db = tmp_path / "nested" / "eval.db"  # parent dir auto-created
    sink = SqliteResultsSink(db_path=str(db))
    await sink.emit(_report(), gate_result=evaluate_gate(_report(), min_mean_score=0.1))

    conn = sqlite3.connect(str(db))
    try:
        reports = conn.execute(
            "SELECT id, scorer, target_name, passed, gate_json FROM eval_reports"
        ).fetchall()
        rows = conn.execute(
            "SELECT report_id, row_id, passed, error, metadata_json FROM eval_rows ORDER BY row_id"
        ).fetchall()
    finally:
        conn.close()

    assert len(reports) == 1
    report_id, scorer, target_name, passed, gate_json = reports[0]
    assert scorer == "exact_match"
    assert target_name == "chat"
    assert passed == 1
    assert json.loads(gate_json)["passed"] is True
    # FK linkage + row fidelity.
    assert len(rows) == 2
    assert all(r[0] == report_id for r in rows)
    assert rows[0][1] == "r1"
    assert json.loads(rows[0][4]) == {"k": "v"}
    assert rows[1][3] == "boom"


async def test_sqlite_results_sink_stores_cost_usd_in_metadata_json_and_a_column(
    tmp_path: Path,
) -> None:
    """Per-row cost stays in ``metadata_json``; the report mean now has a column (spec-0045 B6)."""
    report = EvalReport(
        scorer="cost_budget",
        agent_name="chat",
        dataset_size=1,
        passed=1,
        failed=0,
        errored=0,
        mean_score=1.0,
        duration_ms=1.0,
        rows=[
            EvalRowResult(
                row_id="r1",
                score=1.0,
                passed=True,
                duration_ms=1.0,
                prediction="a",
                expected="a",
                metadata={EVAL_COST_USD_METADATA_KEY: 0.05},
            )
        ],
        target_name="echo",
        mean_cost_usd=0.05,
    )
    db = tmp_path / "eval.db"
    await SqliteResultsSink(db_path=str(db)).emit(report)
    conn = sqlite3.connect(str(db))
    try:
        metadata_json = conn.execute("SELECT metadata_json FROM eval_rows").fetchone()[0]
        columns = {row[1] for row in conn.execute("PRAGMA table_info(eval_reports)").fetchall()}
        mean_cost = conn.execute("SELECT mean_cost_usd FROM eval_reports").fetchone()[0]
    finally:
        conn.close()
    assert json.loads(metadata_json)[EVAL_COST_USD_METADATA_KEY] == 0.05
    assert {"mean_cost_usd", "run_json"} <= columns
    assert mean_cost == 0.05


async def test_sqlite_results_sink_adds_the_new_columns_to_an_older_database(
    tmp_path: Path,
) -> None:
    """A database created before spec-0045 gains the columns in place; its rows survive."""
    db = tmp_path / "legacy.db"
    legacy_schema = """
    CREATE TABLE eval_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, scorer TEXT NOT NULL,
        agent_name TEXT NOT NULL, target_name TEXT NOT NULL, dataset_size INTEGER NOT NULL,
        passed INTEGER NOT NULL, failed INTEGER NOT NULL, errored INTEGER NOT NULL,
        mean_score REAL NOT NULL, duration_ms REAL NOT NULL, gate_json TEXT
    );
    INSERT INTO eval_reports (ts, scorer, agent_name, target_name, dataset_size, passed,
        failed, errored, mean_score, duration_ms, gate_json)
    VALUES ('t', 'exact_match', 'chat', 'chat', 1, 1, 0, 0, 1.0, 1.0, NULL);
    """
    with sqlite3.connect(db) as conn:
        conn.executescript(legacy_schema)
    await SqliteResultsSink(db_path=str(db)).emit(_report())
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT scorer, mean_cost_usd, run_json FROM eval_reports").fetchall()
    assert len(rows) == 2, "the legacy row is kept and the new one appended"
    assert rows[0][1] is None and rows[0][2] is None


async def test_sqlite_results_sink_appends(tmp_path: Path) -> None:
    db = tmp_path / "eval.db"
    sink = SqliteResultsSink(db_path=str(db))
    await sink.emit(_report())
    await sink.emit(_report())
    conn = sqlite3.connect(str(db))
    try:
        count = conn.execute("SELECT COUNT(*) FROM eval_reports").fetchone()[0]
        row_count = conn.execute("SELECT COUNT(*) FROM eval_rows").fetchone()[0]
    finally:
        conn.close()
    assert count == 2
    assert row_count == 4


async def test_sqlite_results_sink_no_gate_stores_null(tmp_path: Path) -> None:
    db = tmp_path / "eval.db"
    await SqliteResultsSink(db_path=str(db)).emit(_report())
    conn = sqlite3.connect(str(db))
    try:
        gate_json = conn.execute("SELECT gate_json FROM eval_reports").fetchone()[0]
    finally:
        conn.close()
    assert gate_json is None


def test_sqlite_results_factory_requires_db_path() -> None:
    with pytest.raises(ConfigError):
        sink_registry.get("sqlite_results")({})


def test_sqlite_results_factory_builds(tmp_path: Path) -> None:
    sink = sink_registry.get("sqlite_results")({"db_path": str(tmp_path / "r.db")})
    assert isinstance(sink, SqliteResultsSink)


def test_sqlite_results_in_registry() -> None:
    assert "sqlite_results" in sink_registry.available()


async def test_sqlite_results_sink_normalises_sqlite_url(tmp_path: Path) -> None:
    """D4 regression: a ``sqlite:///`` URL (as found in ``MANGOMAS_DB__URL`` or
    a baseline's ``db_path`` option) must resolve to the same file a bare path
    would, not create a literal ``sqlite:`` directory next to a stray file.
    """
    db = tmp_path / "eval.db"
    url = f"sqlite:///{db}"
    await SqliteResultsSink(db_path=url).emit(_report())

    assert db.is_file()
    assert not (tmp_path / "sqlite:").exists()
    conn = sqlite3.connect(str(db))
    try:
        count = conn.execute("SELECT COUNT(*) FROM eval_reports").fetchone()[0]
    finally:
        conn.close()
    assert count == 1


async def test_sqlite_results_sink_url_and_bare_path_write_the_same_file(
    tmp_path: Path,
) -> None:
    db = tmp_path / "eval.db"
    await SqliteResultsSink(db_path=str(db)).emit(_report())
    await SqliteResultsSink(db_path=f"sqlite:///{db}").emit(_report())

    conn = sqlite3.connect(str(db))
    try:
        count = conn.execute("SELECT COUNT(*) FROM eval_reports").fetchone()[0]
    finally:
        conn.close()
    assert count == 2


async def test_sqlite_results_sink_memory_url_still_works() -> None:
    # ":memory:" must keep working after URL normalisation is introduced.
    await SqliteResultsSink(db_path=":memory:").emit(_report())


def test_sqlite_results_sink_error_detail_truncate_constant() -> None:
    """D2/D4 regression: the sink's error path shares SQLiteRepository's truncation
    constant. Actual truncation behaviour is covered by
    ``test_sqlite.py::test_persistence_error_detail_is_truncated``; this test just
    pins the shared constant the sink relies on.
    """
    assert DEFAULT_ERROR_DETAIL_TRUNCATE == 200


async def test_sqlite_results_sink_wraps_schema_mismatch_in_persistence_error(
    tmp_path: Path,
) -> None:
    """Pre-creating ``eval_reports`` with the wrong columns makes ``CREATE TABLE IF
    NOT EXISTS`` a no-op and the INSERT fail — the deterministic trigger for the
    ``sqlite3.Error`` branch. Mutation: narrow the ``except`` -> raw
    ``OperationalError``."""
    db = tmp_path / "bad.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE eval_reports (id INTEGER)")
    with pytest.raises(PersistenceError) as excinfo:
        await SqliteResultsSink(db_path=str(db)).emit(_report())
    assert "SQLite" in str(excinfo.value)
    assert excinfo.value.detail
    assert len(excinfo.value.detail) <= DEFAULT_ERROR_DETAIL_TRUNCATE


async def test_sqlite_results_sink_wraps_unwritable_path_in_persistence_error(
    tmp_path: Path,
) -> None:
    """The parent ``mkdir`` used to sit outside the ``try``; a file where a
    directory is expected now surfaces as the typed error."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(PersistenceError):
        await SqliteResultsSink(db_path=str(blocker / "results.db")).emit(_report())


async def test_sqlite_results_sink_wraps_unserialisable_metadata(tmp_path: Path) -> None:
    """``json.dumps(row.metadata)`` raising ``TypeError`` is a sink failure, not a
    bare traceback through the CLI's per-sink isolation."""
    row = EvalRowResult(
        row_id="r1",
        score=1.0,
        passed=True,
        duration_ms=1.0,
        prediction="p",
        expected="e",
        metadata={"unserialisable": object()},
    )
    report = _report()
    report = type(report)(**{**report.__dict__, "rows": [row]})
    with pytest.raises(PersistenceError):
        await SqliteResultsSink(db_path=str(tmp_path / "r.db")).emit(report)
