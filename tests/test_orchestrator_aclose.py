"""Unit tests for :meth:`Orchestrator.aclose`.

Previously the same teardown logic lived in ``cli/main.py::_close_orchestrator``
and two demo scripts. It now lives on :class:`Orchestrator` so every entry
point shares one tested path. These tests pin down each branch:

* LLM has ``aclose`` -> awaited
* LLM lacks ``aclose`` -> skipped without raising
* Repo has ``aclose`` -> awaited, sync ``close`` is NOT called
* Repo lacks ``aclose`` -> sync ``close`` is called instead
* Memory has sync ``close`` -> called
* No repo + no memory -> no-op for those branches
* Idempotency -> second call is a no-op (no double-close)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from mangomas.core import AgentContext, Orchestrator
from mangomas.core.agent import Message
from tests.fakes import FakeLLM, FakeMemoryRepository, FakeRepository


class _AcloseBoom(Exception):
    """Marker exception for tests that need to simulate a failing close hook."""


@dataclass
class _FailingAcloseLLM:
    """LLM stub whose ``aclose`` always raises — exercises partial-close behaviour."""

    closed_attempted: bool = False

    async def complete(
        self,
        messages: list[Message],  # noqa: ARG002
        *,
        temperature: float | None = None,  # noqa: ARG002
    ) -> str:
        return ""

    async def aclose(self) -> None:
        self.closed_attempted = True
        raise _AcloseBoom("simulated LLM teardown failure")


@dataclass
class _AsyncCloseRepo:
    """Stand-in for an async-pool repository (e.g. PostgresRepository)."""

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
class _LLMWithoutAclose:
    """Minimal LLM stub that does NOT expose ``aclose`` — tests the skip branch."""

    calls: list[list[Message]] = field(default_factory=list)

    async def complete(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,  # noqa: ARG002
    ) -> str:
        self.calls.append(list(messages))
        return ""


def _orch_with(
    *,
    llm: object | None = None,
    repo: object | None = None,
    memory: object | None = None,
) -> Orchestrator:
    """Build an Orchestrator with the given context shape; defaults use a fresh FakeLLM."""
    effective_llm = llm if llm is not None else FakeLLM()
    return Orchestrator(
        AgentContext(llm=effective_llm, repo=repo, memory=memory)  # type: ignore[arg-type]
    )


# ── LLM branches ─────────────────────────────────────────────────────────────


async def test_aclose_awaits_llm_aclose_when_available() -> None:
    llm = FakeLLM()
    orch = _orch_with(llm=llm)

    await orch.aclose()

    assert llm.closed is True


async def test_aclose_skips_llm_without_aclose_attribute() -> None:
    llm = _LLMWithoutAclose()
    orch = _orch_with(llm=llm)

    # Must not raise even though llm has no aclose method.
    await orch.aclose()


# ── Repo branches ────────────────────────────────────────────────────────────


async def test_aclose_prefers_repo_aclose_over_sync_close() -> None:
    repo = _AsyncCloseRepo()
    orch = _orch_with(repo=repo)

    await orch.aclose()

    assert repo.closed is True
    assert repo.sync_closed is False


async def test_aclose_falls_back_to_repo_sync_close_when_no_aclose() -> None:
    repo = FakeRepository()
    orch = _orch_with(repo=repo)

    await orch.aclose()

    assert repo.closed is True


# ── Memory branch ────────────────────────────────────────────────────────────


async def test_aclose_closes_memory_when_present() -> None:
    memory = FakeMemoryRepository()
    orch = _orch_with(memory=memory)

    await orch.aclose()

    assert memory.closed is True


# ── No-op branches ───────────────────────────────────────────────────────────


async def test_aclose_with_no_repo_or_memory_is_noop() -> None:
    """Only the LLM gets closed; repo/memory branches are skipped without raising."""
    llm = FakeLLM()
    orch = _orch_with(llm=llm)

    await orch.aclose()

    assert llm.closed is True


# ── Idempotency ──────────────────────────────────────────────────────────────


async def test_aclose_is_idempotent() -> None:
    """A second aclose() call must not re-invoke component close hooks."""
    llm = FakeLLM()
    repo = FakeRepository()
    memory = FakeMemoryRepository()
    orch = _orch_with(llm=llm, repo=repo, memory=memory)

    await orch.aclose()

    # Mutate the flags so a second close would visibly re-toggle them.
    llm.closed = False
    repo.closed = False
    memory.closed = False

    await orch.aclose()

    assert llm.closed is False, "second aclose() must not re-close the LLM"
    assert repo.closed is False, "second aclose() must not re-close the repo"
    assert memory.closed is False, "second aclose() must not re-close memory"


# ── Fault tolerance: partial close on hook failure ───────────────────────────


async def test_aclose_runs_all_hooks_even_when_llm_raises() -> None:
    """If LLM aclose raises, repo and memory must still be closed."""
    failing_llm = _FailingAcloseLLM()
    repo = FakeRepository()
    memory = FakeMemoryRepository()
    orch = _orch_with(llm=failing_llm, repo=repo, memory=memory)

    with pytest.raises(_AcloseBoom):
        await orch.aclose()

    assert failing_llm.closed_attempted is True
    assert repo.closed is True, "repo must close even if LLM aclose failed"
    assert memory.closed is True, "memory must close even if LLM aclose failed"


async def test_aclose_latches_closed_flag_after_failure() -> None:
    """A failed first call still latches the closed flag so a second call is a no-op."""
    failing_llm = _FailingAcloseLLM()
    repo = FakeRepository()
    orch = _orch_with(llm=failing_llm, repo=repo)

    with pytest.raises(_AcloseBoom):
        await orch.aclose()

    # Reset the repo flag and call again; the second call must not re-touch anything.
    repo.closed = False
    await orch.aclose()  # must not raise
    assert repo.closed is False, "second aclose() must skip all hooks after a failed first call"
