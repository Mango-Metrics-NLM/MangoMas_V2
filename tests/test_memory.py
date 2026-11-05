"""Tests for the FileMemoryRepository adapter."""

from __future__ import annotations

import asyncio
import pathlib
from collections.abc import Callable

import pytest

from mangomas.adapters.storage.base import MemoryRepository
from mangomas.adapters.storage.memory import FileMemoryRepository
from mangomas.config import MemorySettings
from mangomas.errors import PersistenceError
from tests.fakes import FakeMemoryRepository

# ── FakeMemoryRepository satisfies the MemoryRepository protocol ──────────────


def test_fake_memory_satisfies_protocol() -> None:
    assert isinstance(FakeMemoryRepository(), MemoryRepository)


# ── FakeMemoryRepository write_episodic ───────────────────────────────────────


async def test_fake_memory_write_episodic_appends() -> None:
    mem = FakeMemoryRepository()
    await mem.write_episodic("entry one")
    await mem.write_episodic("entry two")
    assert mem.episodic_entries == ["entry one", "entry two"]


async def test_fake_memory_write_episodic_returns_path_string() -> None:
    mem = FakeMemoryRepository()
    path = await mem.write_episodic("hi", prefix="daily")
    assert isinstance(path, str)
    assert "daily" in path


# ── FakeMemoryRepository read_index / append_index ───────────────────────────


async def test_fake_memory_read_index_empty_by_default() -> None:
    mem = FakeMemoryRepository()
    assert await mem.read_index() == ""


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


async def test_file_memory_write_episodic_creates_file(tmp_path: object) -> None:
    repo = FileMemoryRepository(_settings(tmp_path))
    path = await repo.write_episodic("Hello, memory!")

    assert pathlib.Path(path).exists()
    assert "Hello, memory!" in pathlib.Path(path).read_text()


async def test_file_memory_write_episodic_appends(tmp_path: object) -> None:
    repo = FileMemoryRepository(_settings(tmp_path))
    await repo.write_episodic("line 1")
    path = await repo.write_episodic("line 2")

    content = pathlib.Path(path).read_text()
    assert "line 1" in content
    assert "line 2" in content


async def test_file_memory_write_episodic_prefix(tmp_path: object) -> None:
    repo = FileMemoryRepository(_settings(tmp_path))
    path = await repo.write_episodic("note", prefix="agent")
    assert "agent" in path


async def test_file_memory_read_index_missing(tmp_path: object) -> None:
    repo = FileMemoryRepository(_settings(tmp_path))
    assert await repo.read_index() == ""


async def test_file_memory_append_and_read_index(tmp_path: object) -> None:
    repo = FileMemoryRepository(_settings(tmp_path))
    await repo.append_index("- item one")
    await repo.append_index("- item two")
    content = await repo.read_index()
    assert "- item one" in content
    assert "- item two" in content


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


async def test_file_memory_use_after_close_raises(tmp_path: object) -> None:
    # Mirrors SQLiteRepository: any operation on a closed repository surfaces
    # as PersistenceError instead of silently succeeding.
    repo = FileMemoryRepository(_settings(tmp_path))
    repo.close()
    with pytest.raises(PersistenceError):
        await repo.write_episodic("after close")
    with pytest.raises(PersistenceError):
        await repo.read_index()
    with pytest.raises(PersistenceError):
        await repo.append_index("after close")


async def test_file_memory_write_episodic_does_not_double_a_trailing_newline(
    tmp_path: object,
) -> None:
    path = await FileMemoryRepository(_settings(tmp_path)).write_episodic("already terminated\n")
    assert pathlib.Path(path).read_text() == "already terminated\n"


async def test_file_memory_append_index_does_not_double_a_trailing_newline(
    tmp_path: object,
) -> None:
    repo = FileMemoryRepository(_settings(tmp_path))
    await repo.append_index("- entry\n")
    assert await repo.read_index() == "- entry\n"


@pytest.mark.parametrize("operation", ["write_episodic", "read_index", "append_index"])
async def test_file_memory_close_racing_the_thread_body_raises(
    tmp_path: object, operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The in-thread closed check is a second gate, not a copy of the first.

    ``_ensure_open`` runs on the event loop; the file write runs later on a
    worker thread. A ``close()`` that lands between the two must still be
    honoured, which is why each thread body re-checks under the lock. The
    race is reproduced by closing the repository at the hand-off point.
    """
    repo = FileMemoryRepository(_settings(tmp_path))

    async def _close_then_run(fn: Callable[[], object]) -> object:
        repo.close()
        return fn()

    monkeypatch.setattr(asyncio, "to_thread", _close_then_run)
    call = getattr(repo, operation)
    with pytest.raises(PersistenceError, match="closed"):
        await (call("x") if operation != "read_index" else call())
