"""A dispatch that raises must still leave a durable row (ADR-0031).

The orchestrator-level half of the contract ``tests/test_turn_failure_record.py``
covers at the storage level. The behaviour is exercised against a minimal subclass, and
``test_both_composition_orchestrators_record_failures`` separately pins that
the mixin is actually *in the MRO* of the two classes composition builds — a
mixin that exists and is never wired is the defect this whole audit is about,
and behaviour tests against a hand-assembled subclass cannot see it.
"""

from __future__ import annotations

import inspect

import pytest

from mangomas.adapters.storage._schema import TurnStatus
from mangomas.composition.builder import _Orchestrator
from mangomas.composition.harness import _HarnessOrchestrator
from mangomas.composition.recording import (
    MIN_MAX_STEPS,
    UNTYPED_ERROR_CODE,
    _FailureRecordingMixin,
)
from mangomas.core import AgentContext, Orchestrator
from mangomas.core.agent import AgentRequest, AgentResponse, Message
from mangomas.errors import AgentNotFound, LLMTimeout
from tests.fakes import FakeLLM, FakeRepository


class _RecordingOrchestrator(_FailureRecordingMixin, Orchestrator):
    """The mixin over the bare orchestrator, matching the composition shape."""


class _RaisingAgent:
    name = "boom"

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def handle(self, request: AgentRequest, ctx: AgentContext) -> AgentResponse:  # noqa: ARG002
        raise self._exc


class _OkAgent:
    name = "fine"

    async def handle(self, request: AgentRequest, ctx: AgentContext) -> AgentResponse:  # noqa: ARG002
        return AgentResponse(content="ok", agent=self.name)


def _request() -> AgentRequest:
    return AgentRequest(messages=[Message(role="user", content="hi")])


def _orchestrator(agent: object, repo: FakeRepository) -> _RecordingOrchestrator:
    orch = _RecordingOrchestrator(AgentContext(llm=FakeLLM(), repo=repo))
    orch.register(agent)  # type: ignore[arg-type]
    return orch


async def test_a_typed_failure_is_persisted_with_its_error_code() -> None:
    """A ``MangomasError`` records under its own ``code``, not a generic one."""
    repo = FakeRepository()
    orch = _orchestrator(_RaisingAgent(LLMTimeout("upstream took too long")), repo)

    with pytest.raises(LLMTimeout):
        await orch.dispatch("boom", _request())

    rows = await repo.list_turns()
    assert len(rows) == 1
    assert rows[0]["status"] == TurnStatus.ERROR
    assert rows[0]["error_code"] == "llm_timeout"


async def test_an_untyped_failure_is_still_persisted() -> None:
    """An unmodelled exception is recorded rather than lost.

    A bug that escapes dispatch is exactly the case an audit trail is for, so
    it must not be the case that goes unrecorded.
    """
    repo = FakeRepository()
    orch = _orchestrator(_RaisingAgent(RuntimeError("something unmodelled")), repo)

    with pytest.raises(RuntimeError):
        await orch.dispatch("boom", _request())

    rows = await repo.list_turns()
    assert rows[0]["error_code"] == UNTYPED_ERROR_CODE


async def test_the_original_error_survives_a_broken_repository() -> None:
    """Recording is best-effort; it must never replace the caller's error.

    Losing the real failure to a secondary persistence failure — while
    recording a failure — would be a particularly unhelpful trade.
    """

    class _BrokenRepo(FakeRepository):
        async def save_failed_turn(self, *args: object, **kwargs: object) -> int:
            raise OSError(f"disk gone while recording {args!r} {kwargs!r}")

    orch = _orchestrator(_RaisingAgent(LLMTimeout("upstream slow")), _BrokenRepo())

    with pytest.raises(LLMTimeout):
        await orch.dispatch("boom", _request())


async def test_a_successful_dispatch_records_exactly_one_ok_row() -> None:
    """The other direction: the wrap must not double-write or mislabel success.

    Without this, a mixin that recorded on *every* dispatch would pass all
    three tests above.
    """
    repo = FakeRepository()
    orch = _orchestrator(_OkAgent(), repo)

    await orch.dispatch("fine", _request())

    rows = await repo.list_turns()
    assert len(rows) == 1
    assert rows[0]["status"] == TurnStatus.OK
    assert rows[0]["error_code"] is None


async def test_a_repository_without_the_writer_is_tolerated() -> None:
    """A bare TurnRepository backend keeps working, unrecorded but unbroken."""

    class _BareRepo(FakeRepository):
        save_failed_turn = None  # type: ignore[assignment]

    orch = _orchestrator(_RaisingAgent(LLMTimeout("slow")), _BareRepo())

    with pytest.raises(LLMTimeout):
        await orch.dispatch("boom", _request())


def test_both_composition_orchestrators_record_failures() -> None:
    """Both classes ``build_orchestrator`` can return must carry the mixin.

    The harness-enabled branch is the easy one to forget: it overrides
    ``dispatch`` itself, so it records only because its override delegates
    through ``super()`` and the mixin sits next in the MRO. Pin both, and pin
    the ordering, because a mixin listed *after* the class that defines
    ``dispatch`` would be silently bypassed.
    """
    for cls in (_Orchestrator, _HarnessOrchestrator):
        mro = cls.__mro__
        assert _FailureRecordingMixin in mro, f"{cls.__name__} does not record failures"
        assert mro.index(_FailureRecordingMixin) < mro.index(Orchestrator), (
            f"{cls.__name__} lists the mixin after Orchestrator; its dispatch would win"
        )


async def test_an_unknown_agent_is_not_recorded_as_a_turn() -> None:
    """A routing error is not a turn — nothing ran.

    ``AgentNotFound`` is raised before any agent is invoked, so no side effect
    was possible and the honest answer to "what did this system do?" is
    "nothing". Recording it would also let any caller inflate the turn store by
    requesting agents that do not exist, on a table whose whole value is that
    it describes real work.
    """
    repo = FakeRepository()
    orch = _orchestrator(_OkAgent(), repo)

    with pytest.raises(AgentNotFound):
        await orch.dispatch("nosuch", _request())

    assert await repo.list_turns() == []


async def test_an_error_raised_inside_an_agent_is_still_recorded() -> None:
    """The exclusion must stay narrow.

    An ordinary exception raised *inside* ``handle`` is an execution failure
    however plain its type — that is exactly the case where a tool may already
    have changed something outside this process. Without this, widening
    ``NON_EXECUTION_ERRORS`` to a broad type would go unnoticed.
    """
    repo = FakeRepository()
    orch = _orchestrator(_RaisingAgent(ValueError("bad input from the model")), repo)

    with pytest.raises(ValueError, match="bad input"):
        await orch.dispatch("boom", _request())

    rows = await repo.list_turns()
    assert len(rows) == 1
    assert rows[0]["error_code"] == UNTYPED_ERROR_CODE


async def test_an_invalid_max_steps_is_not_recorded_as_a_turn() -> None:
    """A caller-argument error is not a turn either.

    ``Orchestrator.dispatch`` rejects ``max_steps < 1`` before it looks up the
    agent, so nothing is dispatched. Recording it would reopen the same
    write-amplification ``NON_EXECUTION_ERRORS`` exists to close.
    """
    repo = FakeRepository()
    orch = _orchestrator(_OkAgent(), repo)

    with pytest.raises(ValueError, match="max_steps"):
        await orch.dispatch("fine", _request(), max_steps=0)

    assert await repo.list_turns() == []


def test_the_mixin_and_the_orchestrator_agree_on_the_max_steps_bound() -> None:
    """Pin the deliberate duplication of the ``max_steps`` lower bound.

    The mixin re-checks the bound so the error is raised *outside* its
    recording try. Discriminating on the exception type instead would be wrong:
    a ``ValueError`` from inside an agent's ``handle`` is an execution failure
    and must still be recorded. The cost of that choice is one duplicated
    constant, so this asserts the two cannot drift apart silently — if
    ``Orchestrator``'s bound moves, the mixin would start recording a turn the
    orchestrator rejects, or reject one it accepts.
    """
    source = inspect.getsource(Orchestrator.dispatch)

    assert f"max_steps < {MIN_MAX_STEPS}" in source, (
        "Orchestrator.dispatch no longer rejects max_steps below "
        f"{MIN_MAX_STEPS}; composition.recording.MIN_MAX_STEPS must follow it"
    )
