"""Contract tests for the shared test doubles in ``tests/fakes.py``.

The fakes are infrastructure the whole suite stands on, so a defect in one is
a defect in every test that uses it — and it surfaces as an unrelated failure
somewhere else. This file tests the fakes themselves.

Scoped to the behaviour that is easy to get subtly wrong and impossible to
attribute when it breaks: ``FakeLLM.delay_seconds`` (spec-0029 R8), whose
default must be a genuine no-op rather than a zero-length await. Every other
fake behaviour is already exercised, by construction, by the suites that
consume it.
"""

from __future__ import annotations

import asyncio

import pytest

from mangomas.core.agent import Message
from tests.constants import (
    SLOW_AGENT_DELAY_SECONDS,
    STUB_REPLY,
    TINY_STEP_TIMEOUT_SECONDS,
    UNTIMED_AGENT_DELAY_SECONDS,
)
from tests.fakes import FakeLLM

_MESSAGES = [Message(role="user", content="x")]


async def test_default_fake_llm_never_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default must skip the sleep entirely, not await a zero delay.

    Asserting "no sleep call at all" rather than "the call was fast" is what
    makes this mutation-sensitive: replacing the ``if self.delay_seconds > 0``
    guard with an unconditional ``await asyncio.sleep(self.delay_seconds)``
    keeps every timing-based assertion green (a zero sleep is instant) but
    inserts an event-loop yield into a code path hundreds of existing tests
    drive. This test fails on that mutation; a duration assertion would not.
    """
    slept: list[float] = []

    async def _record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _record)

    llm = FakeLLM()
    assert await llm.complete(_MESSAGES) == STUB_REPLY
    stream = await llm.stream(_MESSAGES)
    assert [chunk async for chunk in stream] == [STUB_REPLY]

    assert slept == [], "the default FakeLLM must not call asyncio.sleep at all"


async def test_delay_seconds_makes_complete_outlast_a_tiny_budget() -> None:
    """A delayed ``complete`` is cancellable by an enclosing timeout.

    This is the property the tier-1 step-timeout flow depends on: the delay has
    to be a real ``await`` that ``asyncio.timeout`` can cancel, not a busy
    wait. The nominal delay is never actually waited out — the expiring timeout
    cancels it — so the test is fast on every machine.
    """
    llm = FakeLLM(delay_seconds=SLOW_AGENT_DELAY_SECONDS)

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(TINY_STEP_TIMEOUT_SECONDS):
            await llm.complete(_MESSAGES)


async def test_delay_seconds_applies_before_the_first_stream_chunk() -> None:
    """The stream path honours the delay too, and still yields its chunks."""
    llm = FakeLLM(delay_seconds=SLOW_AGENT_DELAY_SECONDS)

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(TINY_STEP_TIMEOUT_SECONDS):
            stream = await llm.stream(_MESSAGES)
            [chunk async for chunk in stream]


async def test_a_delay_under_the_budget_still_completes() -> None:
    """The other direction: a delay the budget tolerates must not be cancelled.

    Without this, a mutation that raised the delay unconditionally (or dropped
    the timeout entirely) would leave the two tests above green while breaking
    the case they exist to bound.
    """
    llm = FakeLLM(delay_seconds=UNTIMED_AGENT_DELAY_SECONDS)

    async with asyncio.timeout(SLOW_AGENT_DELAY_SECONDS):
        assert await llm.complete(_MESSAGES) == STUB_REPLY
