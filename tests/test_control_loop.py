"""Tests for the iterative control loop in the orchestrator."""

from __future__ import annotations

import pytest

from mangomas.agents import ChatAgent
from mangomas.core import AgentContext, AgentRequest, AgentResponse, Message, Orchestrator
from mangomas.errors import MaxStepsExceeded
from tests.constants import STUB_REPLY
from tests.fakes import FakeLLM, FakeRepository


def _make_orch(
    llm: FakeLLM | None = None,
    repo: FakeRepository | None = None,
) -> Orchestrator:
    ctx = AgentContext(llm=llm or FakeLLM(), repo=repo)
    orch = Orchestrator(ctx)
    orch.register(ChatAgent())
    return orch


# ── Single-step default — existing behaviour unchanged ────────────────────────


@pytest.mark.asyncio
async def test_single_step_default_unchanged() -> None:
    orch = _make_orch()
    req = AgentRequest(messages=[Message(role="user", content="hello")])
    resp = await orch.dispatch("chat", req)
    assert resp.agent == "chat"
    assert resp.content == STUB_REPLY
    assert resp.metadata["loop"]["steps_taken"] == 1
    assert resp.metadata["loop"]["accepted"] is False


# ── Loop exits early when acceptance_fn satisfied before max ──────────────────


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_loop_raises_max_steps_exceeded() -> None:
    orch = _make_orch()
    req = AgentRequest(messages=[Message(role="user", content="x")])
    with pytest.raises(MaxStepsExceeded) as exc_info:
        await orch.dispatch("chat", req, acceptance_fn=lambda _: False, max_steps=3)
    assert exc_info.value.steps == 3


# ── No exception when no acceptance_fn — just runs max_steps times ───────────


@pytest.mark.asyncio
async def test_no_acceptance_fn_runs_max_steps_without_raising() -> None:
    llm = FakeLLM()
    orch = _make_orch(llm=llm)
    req = AgentRequest(messages=[Message(role="user", content="x")])
    resp = await orch.dispatch("chat", req, max_steps=3)
    assert len(llm.calls) == 3
    assert resp.metadata["loop"]["accepted"] is False


# ── Assistant reply re-injected as context for next step ─────────────────────


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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
    assert len(repo._turns) == 1  # noqa: SLF001


# ── Loop metadata in response ─────────────────────────────────────────────────


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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
