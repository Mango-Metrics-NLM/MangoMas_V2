"""Tests for the iterative control loop in the orchestrator."""

from __future__ import annotations

import asyncio

import pytest

from mangomas.agents import ChatAgent
from mangomas.config import LoopSettings
from mangomas.core import AgentContext, AgentRequest, AgentResponse, Message, Orchestrator
from mangomas.errors import MaxStepsExceeded, StepTimeout
from tests.constants import (
    DEFAULT_LOOP_MAX_STEPS,
    SLOW_AGENT_DELAY_SECONDS,
    STUB_REPLY,
    TINY_STEP_TIMEOUT_SECONDS,
    UNTIMED_AGENT_DELAY_SECONDS,
)
from tests.fakes import FakeLLM, FakeRepository


def _make_orch(
    llm: FakeLLM | None = None,
    repo: FakeRepository | None = None,
    loop_settings: LoopSettings | None = None,
) -> Orchestrator:
    ctx = AgentContext(llm=llm or FakeLLM(), repo=repo)
    orch = Orchestrator(ctx, loop_settings=loop_settings)
    orch.register(ChatAgent())
    return orch


# ── Single-step default — existing behaviour unchanged ────────────────────────


async def test_single_step_default_unchanged() -> None:
    orch = _make_orch()
    req = AgentRequest(messages=[Message(role="user", content="hello")])
    resp = await orch.dispatch("chat", req)
    assert resp.agent == "chat"
    assert resp.content == STUB_REPLY
    assert resp.metadata["loop"]["steps_taken"] == 1
    assert resp.metadata["loop"]["accepted"] is False


# ── Loop exits early when acceptance_fn satisfied before max ──────────────────


async def test_loop_exits_on_acceptance_before_max() -> None:
    llm = FakeLLM(replies=["no", "no", "yes", "never"])
    orch = _make_orch(llm=llm)
    req = AgentRequest(messages=[Message(role="user", content="go")])

    call_count = 0

    def accept(r: AgentResponse) -> bool:
        nonlocal call_count
        call_count += 1
        return r.content == "yes"

    resp = await orch.dispatch("chat", req, acceptance_fn=accept, max_steps=5)
    assert resp.metadata["loop"]["steps_taken"] == 3
    assert resp.metadata["loop"]["accepted"] is True
    assert len(llm.calls) == 3


# ── MaxStepsExceeded raised when acceptance_fn never satisfied ────────────────


async def test_loop_raises_max_steps_exceeded() -> None:
    orch = _make_orch()
    req = AgentRequest(messages=[Message(role="user", content="x")])
    with pytest.raises(MaxStepsExceeded) as exc_info:
        await orch.dispatch("chat", req, acceptance_fn=lambda _: False, max_steps=3)
    assert exc_info.value.steps == 3


# ── No exception when no acceptance_fn — just runs max_steps times ───────────


async def test_no_acceptance_fn_runs_max_steps_without_raising() -> None:
    llm = FakeLLM()
    orch = _make_orch(llm=llm)
    req = AgentRequest(messages=[Message(role="user", content="x")])
    resp = await orch.dispatch("chat", req, max_steps=3)
    assert len(llm.calls) == 3
    assert resp.metadata["loop"]["accepted"] is False


# ── Assistant reply re-injected as context for next step ─────────────────────


async def test_loop_reinjects_assistant_message() -> None:
    llm = FakeLLM(replies=["first-reply", "second-reply"])
    orch = _make_orch(llm=llm)
    req = AgentRequest(messages=[Message(role="user", content="start")])

    call_count = 0

    def accept(_r: AgentResponse) -> bool:
        nonlocal call_count
        call_count += 1
        return call_count >= 2

    await orch.dispatch("chat", req, acceptance_fn=accept, max_steps=5)

    # Second LLM call must include the first assistant reply in the message list.
    second_call_messages = llm.calls[1]
    assert any(m.role == "assistant" and m.content == "first-reply" for m in second_call_messages)


# ── Turn persisted exactly once (on the final step) ──────────────────────────


async def test_loop_persists_final_turn_only() -> None:
    repo = FakeRepository()
    orch = _make_orch(repo=repo)
    req = AgentRequest(messages=[Message(role="user", content="x")])

    call_count = 0

    def accept(_r: AgentResponse) -> bool:
        nonlocal call_count
        call_count += 1
        return call_count >= 3

    await orch.dispatch("chat", req, acceptance_fn=accept, max_steps=5)
    assert len(repo._turns) == 1


# ── Loop metadata in response ─────────────────────────────────────────────────


async def test_loop_metadata_reflects_actual_steps() -> None:
    orch = _make_orch()
    req = AgentRequest(messages=[Message(role="user", content="x")])

    call_count = 0

    def accept(_r: AgentResponse) -> bool:
        nonlocal call_count
        call_count += 1
        return call_count >= 2

    resp = await orch.dispatch("chat", req, acceptance_fn=accept, max_steps=10)
    assert resp.metadata["loop"]["steps_taken"] == 2
    assert resp.metadata["loop"]["accepted"] is True


# ── max_steps kwarg overrides request.max_steps ───────────────────────────────


async def test_dispatch_max_steps_kwarg_overrides_request() -> None:
    orch = _make_orch()
    req = AgentRequest(
        messages=[Message(role="user", content="x")],
        max_steps=10,
    )
    with pytest.raises(MaxStepsExceeded) as exc_info:
        await orch.dispatch("chat", req, acceptance_fn=lambda _: False, max_steps=2)
    # kwarg wins: raises after 2, not 10
    assert exc_info.value.steps == 2


# ── LoopSettings wiring (spec-0026): per-step timeout ─────────────────────────


class _SlowAgent:
    """Agent whose handle() sleeps — the target of the per-step timeout."""

    name = "slow"

    def __init__(self, delay: float) -> None:
        self._delay = delay

    async def handle(self, _request: AgentRequest, _ctx: AgentContext) -> AgentResponse:
        await asyncio.sleep(self._delay)
        return AgentResponse(content=STUB_REPLY, agent=self.name)


async def test_step_timeout_raises_step_timeout_and_persists_nothing() -> None:
    """A step slower than the configured budget raises the typed StepTimeout.

    Mutation proof (spec-0026): removing the ``asyncio.timeout`` wrap in
    ``Orchestrator._handle_step`` makes this test fail (the slow agent
    completes and no StepTimeout is raised).
    """
    repo = FakeRepository()
    orch = _make_orch(
        repo=repo,
        loop_settings=LoopSettings(step_timeout_seconds=TINY_STEP_TIMEOUT_SECONDS),
    )
    orch.register(_SlowAgent(SLOW_AGENT_DELAY_SECONDS))
    req = AgentRequest(messages=[Message(role="user", content="x")])

    with pytest.raises(StepTimeout) as exc_info:
        await orch.dispatch("slow", req)
    assert exc_info.value.seconds == TINY_STEP_TIMEOUT_SECONDS
    # The timeout aborts the loop before the persistence step — no half turn.
    assert repo._turns == []


async def test_no_loop_settings_means_no_timeout_regression_pin() -> None:
    """Without loop_settings a slow step completes exactly as before spec-0026."""
    orch = _make_orch(loop_settings=None)
    orch.register(_SlowAgent(UNTIMED_AGENT_DELAY_SECONDS))
    req = AgentRequest(messages=[Message(role="user", content="x")])
    resp = await orch.dispatch("slow", req)
    assert resp.content == STUB_REPLY


async def test_default_step_timeout_does_not_bite_a_fast_agent() -> None:
    """The composition-wired default (30 s) leaves a normal step untouched."""
    repo = FakeRepository()
    orch = _make_orch(repo=repo, loop_settings=LoopSettings())
    req = AgentRequest(messages=[Message(role="user", content="x")])
    resp = await orch.dispatch("chat", req)
    assert resp.content == STUB_REPLY
    assert len(repo._turns) == 1


# ── LoopSettings wiring (spec-0026): max_steps precedence ─────────────────────


async def test_settings_max_steps_fills_caller_silence() -> None:
    """Tier 3: no kwarg + default request.max_steps → settings cap the loop."""
    llm = FakeLLM()
    orch = _make_orch(llm=llm, loop_settings=LoopSettings(max_steps=3))
    req = AgentRequest(messages=[Message(role="user", content="x")])
    resp = await orch.dispatch("chat", req)
    assert len(llm.calls) == 3
    assert resp.metadata["loop"]["steps_taken"] == 3


async def test_non_default_request_max_steps_beats_settings() -> None:
    """Tier 2: an explicit request.max_steps is caller intent over the env cap."""
    llm = FakeLLM()
    orch = _make_orch(llm=llm, loop_settings=LoopSettings(max_steps=5))
    req = AgentRequest(messages=[Message(role="user", content="x")], max_steps=2)
    resp = await orch.dispatch("chat", req)
    assert len(llm.calls) == 2
    assert resp.metadata["loop"]["steps_taken"] == 2


async def test_kwarg_beats_request_and_settings() -> None:
    """Tier 1: the explicit kwarg wins over both lower tiers."""
    orch = _make_orch(loop_settings=LoopSettings(max_steps=5))
    req = AgentRequest(messages=[Message(role="user", content="x")], max_steps=4)
    with pytest.raises(MaxStepsExceeded) as exc_info:
        await orch.dispatch("chat", req, acceptance_fn=lambda _: False, max_steps=2)
    assert exc_info.value.steps == 2


async def test_field_default_closes_the_chain_without_settings() -> None:
    """Tier 4: no kwarg, default request, no settings → single shot (as ever)."""
    llm = FakeLLM()
    orch = _make_orch(llm=llm, loop_settings=None)
    req = AgentRequest(messages=[Message(role="user", content="x")])
    resp = await orch.dispatch("chat", req)
    assert len(llm.calls) == DEFAULT_LOOP_MAX_STEPS
    assert resp.metadata["loop"]["steps_taken"] == DEFAULT_LOOP_MAX_STEPS


async def test_settings_default_coincidence_pin() -> None:
    """LoopSettings() defaults (max_steps=1) are behaviour-identical to today.

    The whole backwards-compat argument of spec-0026 rests on this
    coincidence, so it gets its own pin.
    """
    llm = FakeLLM()
    orch = _make_orch(llm=llm, loop_settings=LoopSettings())
    req = AgentRequest(messages=[Message(role="user", content="x")])
    resp = await orch.dispatch("chat", req)
    assert len(llm.calls) == DEFAULT_LOOP_MAX_STEPS
    assert resp.metadata["loop"]["steps_taken"] == DEFAULT_LOOP_MAX_STEPS
    assert resp.metadata["loop"]["accepted"] is False
