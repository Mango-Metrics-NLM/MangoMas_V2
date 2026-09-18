"""Tests for the deterministic clock seam (mangomas.utils.clock).

Guards:
1. ``now()`` returns a timezone-aware UTC datetime.
2. The seam is patchable — monkeypatching ``mangomas.utils.clock.now`` in a
   consumer freezes time as expected.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from mangomas.adapters.storage.memory import FileMemoryRepository
from mangomas.config import MemorySettings
from mangomas.utils import clock


def test_now_returns_aware_utc_datetime() -> None:
    """The clock must return a timezone-aware datetime in UTC."""
    result = clock.now()
    assert isinstance(result, datetime)
    assert result.tzinfo is not None
    assert result.tzinfo == UTC


def test_clock_is_patchable_through_module(tmp_path: Path) -> None:
    """Monkeypatching the clock seam freezes time for consumers."""
    settings = MemorySettings(memory_dir=str(tmp_path), index_file="index.md")
    repo = FileMemoryRepository(settings)

    frozen = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    with patch.object(clock, "now", return_value=frozen):
        # We access a private helper here to observe the patched clock.
        path = repo._episodic_path(prefix="test")
        assert path.name == "test-2026-01-01.md"

        # Test sqlite sink uses the mocked clock too
        from mangomas.eval.sinks.sqlite_results import SqliteResultsSink
        from mangomas.eval.runner import EvalReport

        db_path = str(tmp_path / "test.db")
        sink = SqliteResultsSink(db_path=db_path)

        report = EvalReport(
            scorer="test",
            agent_name="agent",
            target_name="target",
            dataset_size=1,
            passed=1,
            failed=0,
            errored=0,
            mean_score=1.0,
            duration_ms=10.0,
        )

        sink._write(report, None)

        import sqlite3

        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT ts FROM eval_reports LIMIT 1")
        row = cursor.fetchone()
        assert row[0] == "2026-01-01T00:00:00+00:00"
