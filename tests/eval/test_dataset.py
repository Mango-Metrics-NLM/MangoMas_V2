"""Tests for the JSONL dataset loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from mangomas.eval.dataset import DatasetError, load_jsonl


async def test_load_jsonl_mixed_fixture(fixtures_dir: Path) -> None:
    rows = await load_jsonl(fixtures_dir / "mixed.jsonl")
    assert len(rows) == 3
    assert rows[0].id == "m1"
    assert rows[0].expected == "stub-reply"
    assert rows[2].metadata == {"category": "smoke"}


async def test_load_jsonl_empty_file_returns_empty_list(fixtures_dir: Path) -> None:
    rows = await load_jsonl(fixtures_dir / "empty.jsonl")
    assert rows == []


async def test_load_jsonl_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(DatasetError):
        await load_jsonl(tmp_path / "does-not-exist.jsonl")


async def test_load_jsonl_malformed_json_raises(tmp_path: Path) -> None:
    target = tmp_path / "bad.jsonl"
    target.write_text("this is not json\n", encoding="utf-8")
    with pytest.raises(DatasetError):
        await load_jsonl(target)


async def test_load_jsonl_missing_messages_raises(tmp_path: Path) -> None:
    target = tmp_path / "bad.jsonl"
    target.write_text('{"expected": "x"}\n', encoding="utf-8")
    with pytest.raises(DatasetError):
        await load_jsonl(target)


async def test_load_jsonl_missing_expected_raises(tmp_path: Path) -> None:
    target = tmp_path / "bad.jsonl"
    target.write_text(
        '{"messages": [{"role": "user", "content": "x"}]}\n',
        encoding="utf-8",
    )
    with pytest.raises(DatasetError):
        await load_jsonl(target)


async def test_load_jsonl_assigns_id_when_absent(tmp_path: Path) -> None:
    target = tmp_path / "no_ids.jsonl"
    target.write_text(
        '{"messages": [{"role": "user", "content": "x"}], "expected": "y"}\n',
        encoding="utf-8",
    )
    rows = await load_jsonl(target)
    assert rows[0].id == "row-1"


async def test_load_jsonl_skips_blank_lines(tmp_path: Path) -> None:
    target = tmp_path / "blanks.jsonl"
    target.write_text(
        '\n{"id": "a", "messages": [{"role": "user", "content": "x"}], "expected": "y"}\n\n',
        encoding="utf-8",
    )
    rows = await load_jsonl(target)
    assert len(rows) == 1


async def test_load_jsonl_invalid_metadata_type_raises(tmp_path: Path) -> None:
    target = tmp_path / "bad.jsonl"
    target.write_text(
        '{"messages": [{"role": "user", "content": "x"}], '
        '"expected": "y", "metadata": "not-a-dict"}\n',
        encoding="utf-8",
    )
    with pytest.raises(DatasetError):
        await load_jsonl(target)


async def test_load_jsonl_invalid_message_entry_raises(tmp_path: Path) -> None:
    target = tmp_path / "bad.jsonl"
    target.write_text(
        '{"messages": [{"role": "bogus", "content": "x"}], "expected": "y"}\n',
        encoding="utf-8",
    )
    with pytest.raises(DatasetError):
        await load_jsonl(target)
