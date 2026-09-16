"""A dispatch that raises must still leave a durable row (ADR-0031).

The orchestrator-level half of the contract ``tests/test_turn_failure_record.py``
covers at the storage level. The behaviour is exercised against a minimal subclass, and
``test_both_composition_orchestrators_record_failures`` separately pins that
the mixin is actually *in the MRO* of the two classes composition builds — a
mixin that exists and is never wired is the defect this whole audit is about,
and behaviour tests against a hand-assembled subclass cannot see it.
"""

from __future__ import annotations

import pytest

from mangomas.adapters.storage._schema import TurnStatus
from mangomas.composition.builder import _Orchestrator
from mangomas.composition.harness import _HarnessOrchestrator
from mangomas.composition.recording import UNTYPED_ERROR_CODE, _FailureRecordingMixin
from mangomas.core import AgentContext, Orchestrator
from mangomas.core.agent import AgentRequest, AgentResponse, Message
from mangomas.errors import LLMTimeout
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
