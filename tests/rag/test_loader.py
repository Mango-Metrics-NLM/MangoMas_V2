"""Tests for the RAG document loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from mangomas.errors import ConfigError
from mangomas.rag.loader import RawDoc, load_documents


async def test_load_single_file(tmp_path: Path) -> None:
    f = tmp_path / "doc.txt"
    f.write_text("hello world", encoding="utf-8")
    docs = await load_documents(str(f))
    assert docs == [RawDoc(source=f.as_posix(), text="hello world")]


async def test_load_directory_globs_txt_and_md(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    (tmp_path / "b.md").write_text("bravo", encoding="utf-8")
    (tmp_path / "ignore.log").write_text("nope", encoding="utf-8")
    docs = await load_documents(str(tmp_path))
    assert [(d.source, d.text) for d in docs] == [("a.txt", "alpha"), ("b.md", "bravo")]


async def test_load_directory_recurses_with_relative_sources(tmp_path: Path) -> None:
    nested = tmp_path / "sub" / "deep"
    nested.mkdir(parents=True)
    (nested / "c.md").write_text("charlie", encoding="utf-8")
    docs = await load_documents(str(tmp_path))
    assert [d.source for d in docs] == ["sub/deep/c.md"]


async def test_load_empty_directory_returns_empty(tmp_path: Path) -> None:
    assert await load_documents(str(tmp_path)) == []


async def test_load_missing_path_raises_config_error(tmp_path: Path) -> None:
    missing = tmp_path / "nope.txt"
    with pytest.raises(ConfigError):
        await load_documents(str(missing))
