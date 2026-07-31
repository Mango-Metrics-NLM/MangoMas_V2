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
from typing import Any

import pytest

from mangomas.core import AgentContext, Orchestrator
from mangomas.core.agent import Message
from tests.fakes import (
    FakeEmbeddingClient,
    FakeLLM,
    FakeMemoryRepository,
    FakeRepository,
    FakeVectorStore,
)


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
    llm: Any = None,
    repo: Any = None,
    memory: Any = None,
    embeddings: Any = None,
    vector_store: Any = None,
) -> Orchestrator:
    """Build an Orchestrator with the given context shape; defaults use a fresh FakeLLM."""
    effective_llm = llm if llm is not None else FakeLLM()
    return Orchestrator(
        AgentContext(
            llm=effective_llm,
            repo=repo,
            memory=memory,
            embeddings=embeddings,
            vector_store=vector_store,
        )
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


# ── Embeddings branch ────────────────────────────────────────────────────────


@dataclass
class _EmbeddingsWithoutAclose:
    """Embeddings stub that does NOT expose ``aclose`` — tests the skip branch."""

    async def embed(self, text: str) -> list[float]:  # noqa: ARG002
        return [0.0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]


@dataclass
class _FailingAcloseEmbeddings:
    """Embeddings stub whose ``aclose`` raises — exercises the embeddings except branch."""

    closed_attempted: bool = False

    async def embed(self, text: str) -> list[float]:  # noqa: ARG002
        return [0.0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    async def aclose(self) -> None:
        self.closed_attempted = True
        raise _AcloseBoom("simulated embeddings teardown failure")


async def test_aclose_closes_embeddings_when_present() -> None:
    embeddings = FakeEmbeddingClient()
    orch = _orch_with(embeddings=embeddings)

    await orch.aclose()

    assert embeddings.closed is True


async def test_aclose_skips_embeddings_without_aclose_attribute() -> None:
    orch = _orch_with(embeddings=_EmbeddingsWithoutAclose())

    await orch.aclose()  # must not raise


async def test_aclose_captures_embeddings_close_failure() -> None:
    """Embeddings close failure is captured; memory still closes; error re-raised."""
    llm = FakeLLM()
    embeddings = _FailingAcloseEmbeddings()
    memory = FakeMemoryRepository()
    orch = _orch_with(llm=llm, memory=memory, embeddings=embeddings)

    with pytest.raises(_AcloseBoom):
        await orch.aclose()

    assert llm.closed is True
    assert memory.closed is True, "memory must close before the embeddings failure re-raises"
    assert embeddings.closed_attempted is True


# ── Vector store branch ──────────────────────────────────────────────────────


@dataclass
class _VectorStoreWithoutAclose:
    """Vector store stub that does NOT expose ``aclose`` — tests the skip branch."""

    async def upsert(self, **_kw: object) -> None:
        return None

    async def query(self, **_kw: object) -> list[object]:
        return []

    async def delete_by_source(self, source: str) -> int:  # noqa: ARG002
        return 0


@dataclass
class _FailingAcloseVectorStore:
    """Vector store stub whose ``aclose`` raises — exercises the except branch."""

    closed_attempted: bool = False

    async def upsert(self, **_kw: object) -> None:
        return None

    async def query(self, **_kw: object) -> list[object]:
        return []

    async def delete_by_source(self, source: str) -> int:  # noqa: ARG002
        return 0

    async def aclose(self) -> None:
        self.closed_attempted = True
        raise _AcloseBoom("simulated vector store teardown failure")


async def test_aclose_closes_vector_store_when_present() -> None:
    vector_store = FakeVectorStore()
    orch = _orch_with(vector_store=vector_store)

    await orch.aclose()

    assert vector_store.closed is True


async def test_aclose_skips_vector_store_without_aclose_attribute() -> None:
    orch = _orch_with(vector_store=_VectorStoreWithoutAclose())

    await orch.aclose()  # must not raise


async def test_aclose_captures_vector_store_close_failure() -> None:
    """Vector store close failure is captured; LLM still closes; error re-raised."""
    llm = FakeLLM()
    vector_store = _FailingAcloseVectorStore()
    orch = _orch_with(llm=llm, vector_store=vector_store)

    with pytest.raises(_AcloseBoom):
        await orch.aclose()

    assert llm.closed is True
    assert vector_store.closed_attempted is True


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


# ── Per-hook exception isolation: repo and memory branches ───────────────────


@dataclass
class _FailingSyncCloseRepo:
    """Repo stub whose sync ``close`` raises — exercises repo except branch."""

    closed_attempted: bool = False

    async def save_turn(self, *_args: object, **_kw: object) -> int:
        return 0

    async def list_turns(self, limit: int = 50) -> list[dict[str, object]]:  # noqa: ARG002
        return []

    def close(self) -> None:
        self.closed_attempted = True
        raise _AcloseBoom("simulated sync repo close failure")


@dataclass
class _FailingAsyncCloseRepo:
    """Repo stub whose async ``aclose`` raises — exercises repo except branch."""

    closed_attempted: bool = False

    async def save_turn(self, *_args: object, **_kw: object) -> int:
        return 0

    async def list_turns(self, limit: int = 50) -> list[dict[str, object]]:  # noqa: ARG002
        return []

    async def aclose(self) -> None:
        self.closed_attempted = True
        raise _AcloseBoom("simulated async repo close failure")


@dataclass
class _FailingMemory:
    """Memory stub whose ``close`` raises — exercises memory except branch."""

    closed_attempted: bool = False

    async def write_episodic(self, content: str, *, prefix: str = "") -> str:  # noqa: ARG002
        return ""

    async def read_index(self) -> str:
        return ""

    async def append_index(self, entry: str) -> None:  # noqa: ARG002
        return None

    def close(self) -> None:
        self.closed_attempted = True
        raise _AcloseBoom("simulated memory close failure")


async def test_aclose_continues_when_repo_sync_close_raises() -> None:
    """Repo sync close failure must not block memory close, and must re-raise."""
    repo = _FailingSyncCloseRepo()
    memory = FakeMemoryRepository()
    orch = _orch_with(repo=repo, memory=memory)

    with pytest.raises(_AcloseBoom):
        await orch.aclose()

    assert repo.closed_attempted is True
    assert memory.closed is True, "memory must close even when repo close failed"


async def test_aclose_continues_when_repo_aclose_raises() -> None:
    """Repo async aclose failure must not block memory close, and must re-raise."""
    repo = _FailingAsyncCloseRepo()
    memory = FakeMemoryRepository()
    orch = _orch_with(repo=repo, memory=memory)

    with pytest.raises(_AcloseBoom):
        await orch.aclose()

    assert repo.closed_attempted is True
    assert memory.closed is True, "memory must close even when repo aclose failed"


async def test_aclose_captures_memory_close_failure() -> None:
    """Memory close failure is captured and re-raised after every hook ran."""
    llm = FakeLLM()
    memory = _FailingMemory()
    orch = _orch_with(llm=llm, memory=memory)

    with pytest.raises(_AcloseBoom):
        await orch.aclose()

    assert llm.closed is True, "LLM must close before memory close failure"
    assert memory.closed_attempted is True
