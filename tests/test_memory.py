"""Tests for the FileMemoryRepository adapter."""

from __future__ import annotations

import pathlib

import pytest

from mangomas.adapters.storage.base import MemoryRepository
from mangomas.adapters.storage.memory import FileMemoryRepository
from mangomas.config import MemorySettings
from tests.fakes import FakeMemoryRepository

# ── FakeMemoryRepository satisfies the MemoryRepository protocol ──────────────


def test_fake_memory_satisfies_protocol() -> None:
    assert isinstance(FakeMemoryRepository(), MemoryRepository)


# ── FakeMemoryRepository write_episodic ───────────────────────────────────────


@pytest.mark.asyncio
async def test_fake_memory_write_episodic_appends() -> None:
    mem = FakeMemoryRepository()
    await mem.write_episodic("entry one")
    await mem.write_episodic("entry two")
    assert mem.episodic_entries == ["entry one", "entry two"]


@pytest.mark.asyncio
async def test_fake_memory_write_episodic_returns_path_string() -> None:
    mem = FakeMemoryRepository()
    path = await mem.write_episodic("hi", prefix="daily")
    assert isinstance(path, str)
    assert "daily" in path


# ── FakeMemoryRepository read_index / append_index ───────────────────────────


@pytest.mark.asyncio
async def test_fake_memory_read_index_empty_by_default() -> None:
    mem = FakeMemoryRepository()
    assert await mem.read_index() == ""


@pytest.mark.asyncio
async def test_fake_memory_append_index_accumulates() -> None:
    mem = FakeMemoryRepository()
    await mem.append_index("line one")
    await mem.append_index("line two")
    index = await mem.read_index()
    assert "line one" in index
    assert "line two" in index


# ── FakeMemoryRepository close ────────────────────────────────────────────────


def test_fake_memory_close_sets_flag() -> None:
    mem = FakeMemoryRepository()
    assert not mem.closed
    mem.close()
    assert mem.closed


# ── FileMemoryRepository (tmp_path) ──────────────────────────────────────────


def _settings(tmp_path: object) -> MemorySettings:
    return MemorySettings(
        enabled=True,
        memory_dir=str(pathlib.Path(str(tmp_path)) / "memory"),
        index_file="INDEX.md",
    )


@pytest.mark.asyncio
async def test_file_memory_write_episodic_creates_file(tmp_path: object) -> None:
    repo = FileMemoryRepository(_settings(tmp_path))
    path = await repo.write_episodic("Hello, memory!")

    assert pathlib.Path(path).exists()
    assert "Hello, memory!" in pathlib.Path(path).read_text()


@pytest.mark.asyncio
async def test_file_memory_write_episodic_appends(tmp_path: object) -> None:
    repo = FileMemoryRepository(_settings(tmp_path))
    await repo.write_episodic("line 1")
    path = await repo.write_episodic("line 2")

    content = pathlib.Path(path).read_text()
    assert "line 1" in content
    assert "line 2" in content


@pytest.mark.asyncio
async def test_file_memory_write_episodic_prefix(tmp_path: object) -> None:
    repo = FileMemoryRepository(_settings(tmp_path))
    path = await repo.write_episodic("note", prefix="agent")
    assert "agent" in path


@pytest.mark.asyncio
async def test_file_memory_read_index_missing(tmp_path: object) -> None:
    repo = FileMemoryRepository(_settings(tmp_path))
    assert await repo.read_index() == ""


@pytest.mark.asyncio
async def test_file_memory_append_and_read_index(tmp_path: object) -> None:
    repo = FileMemoryRepository(_settings(tmp_path))
    await repo.append_index("- item one")
    await repo.append_index("- item two")
    content = await repo.read_index()
    assert "- item one" in content
    assert "- item two" in content


@pytest.mark.asyncio
async def test_file_memory_creates_parent_dirs(tmp_path: object) -> None:
    settings = MemorySettings(
        enabled=True,
        memory_dir=str(pathlib.Path(str(tmp_path)) / "deep" / "nested" / "memory"),
        index_file="IDX.md",
    )
    repo = FileMemoryRepository(settings)
    await repo.write_episodic("create the dirs")
    assert pathlib.Path(settings.memory_dir).exists()


def test_file_memory_close_sets_flag(tmp_path: object) -> None:
    repo = FileMemoryRepository(_settings(tmp_path))
    repo.close()
    assert repo._closed
