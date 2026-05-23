"""Tests for the CLI's adapter close path (v0.3.0).

The CLI now wraps every command in ``try/finally: asyncio.run(_close_orchestrator(orch))``
so async-pool backends like :class:`PostgresRepository` don't leak
connections at process exit. This module verifies the dispatch logic
without needing a real Postgres pool.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from mangomas.cli.main import _close_orchestrator
from mangomas.core import AgentContext, Orchestrator
from tests.fakes import FakeLLM, FakeMemoryRepository, FakeRepository


@dataclass
class _AsyncCloseRepo:
    """Stand-in for an AsyncCloseableRepository (PostgresRepository shape)."""

    closed: bool = False
    sync_closed: bool = False

    async def save_turn(self, *_args: object, **_kw: object) -> int:
        return 0

    async def list_turns(self, limit: int = 50) -> list[dict[str, object]]:  # noqa: ARG002
        return []

    async def aclose(self) -> None:
        self.closed = True

    def close(self) -> None:
        self.sync_closed = True


@dataclass
class _AsyncCloseLLM:
    closed: bool = False
    calls: list[object] = field(default_factory=list)

    async def complete(self, *_args: object, **_kw: object) -> str:
        return ""

    async def aclose(self) -> None:
        self.closed = True


def _orch_with(*, repo: object) -> Orchestrator:
    llm = _AsyncCloseLLM()
    return Orchestrator(AgentContext(llm=llm, repo=repo))  # type: ignore[arg-type]


def test_close_prefers_aclose_when_repo_supports_it() -> None:
    repo = _AsyncCloseRepo()
    orch = _orch_with(repo=repo)

    asyncio.run(_close_orchestrator(orch))

    assert repo.closed is True
    assert repo.sync_closed is False  # close() must NOT be called when aclose() exists


def test_close_falls_back_to_sync_close_when_no_aclose() -> None:
    repo = FakeRepository()
    orch = _orch_with(repo=repo)

    asyncio.run(_close_orchestrator(orch))

    assert repo.closed is True  # FakeRepository.close() sets this flag


def test_close_closes_llm_via_aclose() -> None:
    llm = FakeLLM()
    orch = Orchestrator(AgentContext(llm=llm, repo=None))

    asyncio.run(_close_orchestrator(orch))

    assert llm.closed is True


def test_close_closes_memory_when_present() -> None:
    memory = FakeMemoryRepository()
    orch = Orchestrator(AgentContext(llm=FakeLLM(), repo=None, memory=memory))

    asyncio.run(_close_orchestrator(orch))

    assert memory.closed is True


def test_close_with_no_repo_or_memory_is_noop() -> None:
    orch = Orchestrator(AgentContext(llm=FakeLLM(), repo=None))
    asyncio.run(_close_orchestrator(orch))  # no exception
