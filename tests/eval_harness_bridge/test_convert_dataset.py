"""Offline tests for the Mango-Mas -> Agents dataset converter."""

from __future__ import annotations

from pathlib import Path

import pytest

import convert_dataset


def test_convert_row_nests_messages_and_agent() -> None:
    row = {
        "id": "m1",
        "messages": [{"role": "user", "content": "x"}],
        "expected": "y",
        "metadata": {"category": "smoke"},
    }
    assert convert_dataset.convert_row(row, agent="chat") == {
        "id": "m1",
        "inputs": {"messages": [{"role": "user", "content": "x"}], "agent": "chat"},
        "expected": "y",
        "metadata": {"category": "smoke"},
    }


def test_convert_row_omits_agent_and_empty_metadata() -> None:
    out = convert_dataset.convert_row(
        {"id": "m2", "messages": [{"role": "user", "content": "x"}], "expected": "y"}
    )
    assert "agent" not in out["inputs"]
    assert "metadata" not in out


def test_convert_row_rejects_missing_messages() -> None:
    with pytest.raises(ValueError, match="non-empty list"):
        convert_dataset.convert_row({"id": "bad", "expected": "y"})


def test_convert_lines_skips_blanks_and_reports_bad_json() -> None:
    good = '{"id": "a", "messages": [{"role": "user", "content": "h"}], "expected": "e"}'
    assert len(convert_dataset.convert_lines([good, "", "   "])) == 1
    with pytest.raises(ValueError, match="line 1: malformed JSON"):
        convert_dataset.convert_lines(["{not json"])


def test_convert_lines_reports_line_number_on_bad_row() -> None:
    good = '{"id": "a", "messages": [{"role": "user", "content": "h"}], "expected": "e"}'
    bad = '{"id": "b", "expected": "e"}'
    with pytest.raises(ValueError, match="line 2: row 'b'"):
        convert_dataset.convert_lines([good, bad])


def test_main_creates_missing_parent_dirs(tmp_path: Path) -> None:
    src = tmp_path / "src.jsonl"
    src.write_text(
        '{"id": "a", "messages": [{"role": "user", "content": "h"}], "expected": "e"}\n',
        encoding="utf-8",
    )
    dst = tmp_path / "nested" / "deeper" / "out.jsonl"
    assert convert_dataset.main([str(src), str(dst), "--agent", "chat"]) == 0
    assert dst.exists()
