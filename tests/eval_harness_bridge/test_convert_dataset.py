"""Offline tests for the Mango-Mas -> Agents dataset converter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import convert_dataset

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DATASETS = _REPO_ROOT / "eval_harness_bridge" / "datasets"

_VALID_ROW = {"id": "m1", "messages": [{"role": "user", "content": "x"}], "expected": "y"}


# --- convert_row: happy paths --------------------------------------------------


def test_convert_row_nests_messages_and_agent() -> None:
    row = {**_VALID_ROW, "metadata": {"category": "smoke"}}
    assert convert_dataset.convert_row(row, agent="chat") == {
        "id": "m1",
        "inputs": {"messages": [{"role": "user", "content": "x"}], "agent": "chat"},
        "expected": "y",
        "metadata": {"category": "smoke"},
    }


def test_convert_row_omits_agent_and_empty_metadata() -> None:
    out = convert_dataset.convert_row(_VALID_ROW)
    assert "agent" not in out["inputs"]
    assert "metadata" not in out


def test_convert_row_assigns_stable_row_index_id_when_missing() -> None:
    out = convert_dataset.convert_row(
        {"messages": [{"role": "user", "content": "x"}], "expected": "y"}, index=7
    )
    assert out["id"] == "row-7"


# --- convert_row: fail-closed validation --------------------------------------


def test_convert_row_rejects_non_object_row() -> None:
    with pytest.raises(ValueError, match="expected a JSON object"):
        convert_dataset.convert_row(["not", "an", "object"])


def test_convert_row_rejects_missing_messages() -> None:
    with pytest.raises(ValueError, match="non-empty list"):
        convert_dataset.convert_row({"id": "bad", "expected": "y"})


def test_convert_row_rejects_malformed_message_entry() -> None:
    with pytest.raises(ValueError, match="malformed message entry"):
        convert_dataset.convert_row(
            {"id": "bad", "messages": [{"role": "wizard", "content": "x"}], "expected": "y"}
        )
    with pytest.raises(ValueError, match="malformed message entry"):
        convert_dataset.convert_row({"id": "bad", "messages": ["nope"], "expected": "y"})


def test_convert_row_rejects_non_string_expected() -> None:
    with pytest.raises(ValueError, match="'expected' must be a string"):
        convert_dataset.convert_row({"id": "bad", "messages": [{"role": "user", "content": "x"}]})


def test_convert_row_rejects_non_object_metadata() -> None:
    with pytest.raises(ValueError, match="'metadata' must be an object"):
        convert_dataset.convert_row({**_VALID_ROW, "metadata": ["nope"]})


# --- convert_lines -------------------------------------------------------------


def test_convert_lines_skips_blanks_and_reports_bad_json() -> None:
    good = json.dumps(_VALID_ROW)
    assert len(convert_dataset.convert_lines([good, "", "   "])) == 1
    with pytest.raises(ValueError, match="line 1: malformed JSON"):
        convert_dataset.convert_lines(["{not json"])


def test_convert_lines_reports_line_number_on_bad_row() -> None:
    good = json.dumps(_VALID_ROW)
    bad = json.dumps({"id": "b", "expected": "e"})
    with pytest.raises(ValueError, match="line 2: row 'b'"):
        convert_dataset.convert_lines([good, bad])


def test_convert_lines_wraps_non_object_row_with_line_number() -> None:
    with pytest.raises(ValueError, match="line 1: expected a JSON object"):
        convert_dataset.convert_lines(["[1, 2, 3]"])


# --- main CLI ------------------------------------------------------------------


def test_main_creates_missing_parent_dirs(tmp_path: Path) -> None:
    src = tmp_path / "src.jsonl"
    src.write_text(json.dumps(_VALID_ROW) + "\n", encoding="utf-8")
    dst = tmp_path / "nested" / "deeper" / "out.jsonl"
    assert convert_dataset.main([str(src), str(dst), "--agent", "chat"]) == 0
    assert dst.exists()
    written = json.loads(dst.read_text(encoding="utf-8").strip())
    assert written["inputs"]["agent"] == "chat"


# --- generated-file drift guard -----------------------------------------------


def test_committed_converted_dataset_is_not_stale() -> None:
    source_lines = (_DATASETS / "mango_suite.source.jsonl").read_text(encoding="utf-8").splitlines()
    expected_rows = convert_dataset.convert_lines(source_lines, agent="chat")
    committed_rows = [
        json.loads(line)
        for line in (_DATASETS / "mango_suite.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert committed_rows == expected_rows, (
        "mango_suite.jsonl is stale — re-run: "
        "python eval_harness_bridge/src/convert_dataset.py "
        "eval_harness_bridge/datasets/mango_suite.source.jsonl "
        "eval_harness_bridge/datasets/mango_suite.jsonl --agent chat"
    )
