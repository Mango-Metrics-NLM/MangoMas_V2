"""Tests for the SQLite eval-results sink."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import mangomas.eval.sinks  # noqa: F401 — registers built-in sinks
from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE
from mangomas.errors import ConfigError
from mangomas.eval import evaluate_gate
from mangomas.eval.runner import EvalReport, EvalRowResult
from mangomas.eval.sink_registry import sink_registry
from mangomas.eval.sinks.sqlite_results import SqliteResultsSink


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
