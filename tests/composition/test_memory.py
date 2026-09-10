"""File-memory factory wiring."""

from __future__ import annotations

from pathlib import Path

from mangomas.composition import _file_memory_factory, build_orchestrator
from mangomas.config import MemorySettings, Settings
from tests.composition.helpers import close_repo


def test_build_orchestrator_with_memory_enabled_attaches_repo(tmp_path: Path) -> None:
    """The ``memory.enabled=True`` branch wires a :class:`FileMemoryRepository`."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.memory.enabled = True
    settings.memory.memory_dir = str(tmp_path / "mem")
    orch = build_orchestrator(settings)
    try:
        assert orch.context.memory is not None
    finally:
        close_repo(orch)


def test_file_memory_factory_returns_repository(tmp_path: Path) -> None:
    """Direct factory smoke test — keeps the factory hook covered for refactors."""
    cfg = MemorySettings(
        enabled=True,
        memory_dir=str(tmp_path / "mem"),
    )
    repo = _file_memory_factory(cfg)
    assert repo is not None
    repo.close()
