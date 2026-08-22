"""Tests for multi-agent topology methods: dispatch_pipeline and dispatch_fan_out.

Spec-0027 adds the whole-pipeline acceptance loop and the settled fan-out
sibling; their tests live here beside the topologies they extend. The span
assertions use the shared module-level ``InMemorySpanExporter`` idiom from
``tests/test_tracing.py`` (the OTel global provider promotes exactly once).
"""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from mangomas.agents import ChatAgent
from mangomas.config import LoopSettings
from mangomas.core import (
    AgentContext,
    AgentRequest,
    AgentResponse,
    FanOutOutcome,
    Message,
    Orchestrator,
)
from mangomas.errors import AgentNotFound, LLMUnavailable, MaxStepsExceeded
from tests.constants import DEFAULT_LOOP_MAX_STEPS
from tests.fakes import FakeLLM

_EXPORTER: InMemorySpanExporter = InMemorySpanExporter()


def _init_exporter() -> None:
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
    else:
        new_provider = TracerProvider()
        new_provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
        trace.set_tracer_provider(new_provider)


_init_exporter()


def _make_orch(llm: FakeLLM | None = None) -> Orchestrator:
    ctx = AgentContext(llm=llm or FakeLLM(), repo=None)
    orch = Orchestrator(ctx)
    orch.register(ChatAgent())
    return orch


def _req(content: str = "go") -> AgentRequest:
    return AgentRequest(messages=[Message(role="user", content=content)])


# ── dispatch_pipeline ─────────────────────────────────────────────────────────


async def test_pipeline_single_agent_passes_through() -> None:
    orch = _make_orch(FakeLLM(reply="hello"))
    resp = await orch.dispatch_pipeline(["chat"], _req())
    assert resp.content == "hello"


async def test_pipeline_threads_output_as_input() -> None:
    llm = FakeLLM(replies=["step-one", "step-two"])
    orch = _make_orch(llm)

    # Register a second chat instance under a different name.
    class ChatAlias(ChatAgent):
        name = "chat2"

    orch.register(ChatAlias())

    resp = await orch.dispatch_pipeline(["chat", "chat2"], _req("start"))
    # Second agent receives "step-one" as the user message.
    second_call = llm.calls[1]
    assert second_call[0].content == "step-one"
    assert resp.content == "step-two"


async def test_pipeline_empty_list_raises_value_error() -> None:
    orch = _make_orch()
    with pytest.raises(ValueError, match="non-empty"):
        await orch.dispatch_pipeline([], _req())


async def test_pipeline_unknown_agent_raises_agent_not_found() -> None:
    orch = _make_orch()
    with pytest.raises(AgentNotFound):
        await orch.dispatch_pipeline(["chat", "ghost"], _req())


async def test_pipeline_preserves_metadata_in_intermediate_request() -> None:
    llm = FakeLLM(replies=["out1", "out2"])
    orch = _make_orch(llm)

    class ChatAlias(ChatAgent):
        name = "chat2"

    orch.register(ChatAlias())
    resp = await orch.dispatch_pipeline(["chat", "chat2"], _req())
    # Final response comes from chat2.
    assert resp.agent == "chat2"


# ── dispatch_fan_out ──────────────────────────────────────────────────────────


async def test_fan_out_single_agent_returns_list_of_one() -> None:
    orch = _make_orch(FakeLLM(reply="one"))
    results = await orch.dispatch_fan_out(["chat"], _req())
    assert len(results) == 1
    assert results[0].content == "one"


async def test_fan_out_parallel_same_agent_both_receive_same_request() -> None:
    llm = FakeLLM(reply="parallel")
    orch = _make_orch(llm)

    class ChatAlias(ChatAgent):
        name = "chat2"

    orch.register(ChatAlias())
    results = await orch.dispatch_fan_out(["chat", "chat2"], _req("query"))
    assert len(results) == 2
    for r in results:
        assert r.content == "parallel"


async def test_fan_out_empty_list_raises_value_error() -> None:
    orch = _make_orch()
    with pytest.raises(ValueError, match="non-empty"):
        await orch.dispatch_fan_out([], _req())


async def test_fan_out_unknown_agent_propagates_immediately() -> None:
    orch = _make_orch()
    with pytest.raises(AgentNotFound):
        await orch.dispatch_fan_out(["chat", "ghost"], _req())


async def test_fan_out_results_order_matches_agent_names() -> None:
    """Results list is in the same order as agent_names."""
    llm = FakeLLM()
    ctx = AgentContext(llm=llm, repo=None)
    orch = Orchestrator(ctx)

    # Register agents that produce distinct outputs by patching reply per call.
    replies = {"a1": "alpha", "a2": "beta"}

    class NamedChat(ChatAgent):
        def __init__(self, agent_name: str, agent_reply: str) -> None:
            super().__init__()
            self.name = agent_name
            self._reply = agent_reply

        async def handle(self, _request: AgentRequest, _ctx: AgentContext) -> AgentResponse:
            return AgentResponse(content=self._reply, agent=self.name)

    for aname, areply in replies.items():
        orch.register(NamedChat(aname, areply))

    results = await orch.dispatch_fan_out(["a1", "a2"], _req())
    assert results[0].content == "alpha"
    assert results[1].content == "beta"


# ── dispatch_pipeline: whole-pipeline acceptance loop (spec-0027) ─────────────


def _make_two_stage_orch(llm: FakeLLM) -> Orchestrator:
    """Pipeline roster ``["chat", "chat2"]`` sharing one FakeLLM reply tape."""
    orch = _make_orch(llm)

    class ChatAlias(ChatAgent):
        name = "chat2"

    orch.register(ChatAlias())
    return orch


async def test_pipeline_acceptance_judges_final_response_and_accepts_on_step_k() -> None:
    """Acceptance runs against the FINAL stage's response; accepted on pass 2.

    Mutation proof (spec-0027 #1): judging the *first* stage's response instead
    makes acceptance see "a1"/"a2" (never "b2"), so the loop exhausts and this
    test fails with MaxStepsExceeded.
    """
    llm = FakeLLM(replies=["a1", "b1", "a2", "b2"])
    orch = _make_two_stage_orch(llm)

    resp = await orch.dispatch_pipeline(
        ["chat", "chat2"],
        _req("start"),
        acceptance_fn=lambda r: r.content == "b2",
        max_steps=3,
    )
    assert resp.content == "b2"
    # Two full pipeline passes: 2 agents x 2 iterations = 4 LLM calls.
    assert len(llm.calls) == 4
    assert resp.metadata["loop"] == {"steps_taken": 2, "accepted": True}


async def test_pipeline_acceptance_exhaustion_raises_max_steps_exceeded() -> None:
    llm = FakeLLM(replies=["a1", "b1", "a2", "b2"])
    orch = _make_two_stage_orch(llm)

    with pytest.raises(MaxStepsExceeded) as exc_info:
        await orch.dispatch_pipeline(
            ["chat", "chat2"], _req(), acceptance_fn=lambda _: False, max_steps=2
        )
    assert exc_info.value.steps == 2
    # The whole pipeline ran twice before exhausting the budget.
    assert len(llm.calls) == 4


async def test_pipeline_defaults_none_single_pass_regression_pin() -> None:
    """No loop kwargs → byte-identical single-pass behaviour (spec-0027 pin).

    The response is exactly what the final inner dispatch produced: content,
    agent, and the *inner* dispatch's own loop block — no pipeline-level
    rewrite, no extra metadata keys, one pass through the roster.
    """
    llm = FakeLLM(replies=["step-one", "step-two"])
    orch = _make_two_stage_orch(llm)

    resp = await orch.dispatch_pipeline(["chat", "chat2"], _req("start"))
    assert len(llm.calls) == 2
    assert resp.model_dump() == {
        "content": "step-two",
        "agent": "chat2",
        "metadata": {"loop": {"steps_taken": 1, "accepted": False}},
    }


async def test_pipeline_max_steps_without_acceptance_iterates_and_returns_last() -> None:
    """max_steps alone mirrors dispatch's no-acceptance multi-step semantics."""
    llm = FakeLLM(replies=["r1", "r2", "r3"])
    orch = _make_orch(llm)

    resp = await orch.dispatch_pipeline(["chat"], _req("go"), max_steps=3)
    assert len(llm.calls) == 3
    assert resp.content == "r3"
    assert resp.metadata["loop"] == {"steps_taken": 3, "accepted": False}
    # Conversational re-injection: pass 3 sees both prior assistant replies.
    third_call = llm.calls[2]
    assistant_contents = [m.content for m in third_call if m.role == "assistant"]
    assert assistant_contents == ["r1", "r2"]


async def test_pipeline_max_steps_of_one_runs_single_pass_with_loop_metadata() -> None:
    """An explicit budget of 1 engages loop mode (metadata block) but no re-run."""
    llm = FakeLLM(reply="only")
    orch = _make_orch(llm)
    resp = await orch.dispatch_pipeline(["chat"], _req(), max_steps=1)
    assert len(llm.calls) == 1
    assert resp.metadata["loop"] == {"steps_taken": 1, "accepted": False}


async def test_pipeline_max_steps_below_one_raises_value_error() -> None:
    orch = _make_orch()
    with pytest.raises(ValueError, match="max_steps"):
        await orch.dispatch_pipeline(["chat"], _req(), max_steps=0)


async def test_pipeline_loop_settings_max_steps_fills_kwarg_silence() -> None:
    """No kwarg → loop_settings.max_steps caps the pipeline loop (tier 2).

    Uses the exhaustion exception's ``steps`` so the assertion is independent
    of the inner dispatches' own settings-driven budgets.
    """
    ctx = AgentContext(llm=FakeLLM(), repo=None)
    orch = Orchestrator(ctx, loop_settings=LoopSettings(max_steps=3))
    orch.register(ChatAgent())

    with pytest.raises(MaxStepsExceeded) as exc_info:
        await orch.dispatch_pipeline(["chat"], _req(), acceptance_fn=lambda _: False)
    assert exc_info.value.steps == 3


async def test_pipeline_acceptance_without_kwarg_or_settings_defaults_to_one() -> None:
    """Tier 3: the AgentRequest field default (1) closes the precedence chain."""
    orch = _make_orch()
    with pytest.raises(MaxStepsExceeded) as exc_info:
        await orch.dispatch_pipeline(["chat"], _req(), acceptance_fn=lambda _: False)
    assert exc_info.value.steps == DEFAULT_LOOP_MAX_STEPS


async def test_pipeline_reinjects_prior_final_reply_into_first_stage() -> None:
    """Iteration 2's first-stage request carries the prior FINAL assistant reply.

    Mutation proofs (spec-0027 #3 and #1): skipping the re-injection append
    drops "b1" from the second pass; appending the first stage's reply instead
    of the final one would inject "a1" — both assertions below discriminate.
    """
    llm = FakeLLM(replies=["a1", "b1", "a2", "b2"])
    orch = _make_two_stage_orch(llm)

    call_count = 0

    def accept(_r: AgentResponse) -> bool:
        nonlocal call_count
        call_count += 1
        return call_count >= 2

    await orch.dispatch_pipeline(
        ["chat", "chat2"], _req("start"), acceptance_fn=accept, max_steps=5
    )

    # llm.calls[2] is iteration 2's first-stage ("chat") call.
    second_pass_first_stage = llm.calls[2]
    assistant_contents = [m.content for m in second_pass_first_stage if m.role == "assistant"]
    assert "b1" in assistant_contents  # the prior FINAL reply was appended
    assert "a1" not in assistant_contents  # never the intermediate stage's reply


# ── dispatch_fan_out_settled (spec-0027) ─────────────────────────────────────


class _BoomAgent:
    """Registered agent whose handle() raises a typed error mid-dispatch."""

    name = "boom"

    async def handle(self, _request: AgentRequest, _ctx: AgentContext) -> AgentResponse:
        raise LLMUnavailable("boom")


class _PlainBoomAgent:
    """Registered agent raising a non-Mangomas error (class-name log path)."""

    name = "plain-boom"

    async def handle(self, _request: AgentRequest, _ctx: AgentContext) -> AgentResponse:
        raise RuntimeError("plain boom")


async def test_fan_out_settled_mixed_outcomes_preserve_roster_order() -> None:
    """Mixed success/failure: outcomes are index-aligned with agent_names.

    Mutation proof (spec-0027 #2): dropping ``return_exceptions=True`` makes
    the ghost's AgentNotFound propagate and discard the sibling successes, so
    this test fails.
    """
    orch = _make_orch(FakeLLM(reply="fine"))
    orch.register(_BoomAgent())

    outcomes = await orch.dispatch_fan_out_settled(["chat", "ghost", "boom"], _req())
    assert [o.agent for o in outcomes] == ["chat", "ghost", "boom"]
    assert [o.ok for o in outcomes] == [True, False, False]

    assert outcomes[0].response is not None
    assert outcomes[0].response.content == "fine"
    assert outcomes[0].error is None

    assert outcomes[1].response is None
    assert isinstance(outcomes[1].error, AgentNotFound)

    assert outcomes[2].response is None
    assert isinstance(outcomes[2].error, LLMUnavailable)


async def test_fan_out_settled_all_success() -> None:
    orch = _make_orch(FakeLLM(reply="ok"))

    class ChatAlias(ChatAgent):
        name = "chat2"

    orch.register(ChatAlias())
    outcomes = await orch.dispatch_fan_out_settled(["chat", "chat2"], _req())
    assert all(o.ok for o in outcomes)
    assert [o.response.content for o in outcomes if o.response is not None] == ["ok", "ok"]


async def test_fan_out_settled_all_fail_including_non_mangomas_error() -> None:
    orch = _make_orch()
    orch.register(_PlainBoomAgent())
    outcomes = await orch.dispatch_fan_out_settled(["ghost", "plain-boom"], _req())
    assert [o.ok for o in outcomes] == [False, False]
    assert isinstance(outcomes[0].error, AgentNotFound)
    assert isinstance(outcomes[1].error, RuntimeError)


def test_fan_out_outcome_requires_exactly_one_of_response_or_error() -> None:
    resp = AgentResponse(content="x", agent="a")
    err = RuntimeError("x")
    with pytest.raises(ValueError, match="exactly one"):
        FanOutOutcome(agent="a")
    with pytest.raises(ValueError, match="exactly one"):
        FanOutOutcome(agent="a", response=resp, error=err)
    # The two valid shapes construct fine and report `ok` accordingly.
    assert FanOutOutcome(agent="a", response=resp).ok is True
    assert FanOutOutcome(agent="a", error=err).ok is False


async def test_fan_out_settled_empty_list_raises_value_error() -> None:
    orch = _make_orch()
    with pytest.raises(ValueError, match="non-empty"):
        await orch.dispatch_fan_out_settled([], _req())


async def test_fan_out_fail_fast_sibling_unchanged_regression_pin() -> None:
    """dispatch_fan_out keeps its fail-fast contract: one bad agent raises and
    the successful sibling's response is discarded (spec-0027 records why the
    settled mode is an additive sibling, not a behaviour change here)."""
    orch = _make_orch(FakeLLM(reply="fine"))
    with pytest.raises(AgentNotFound):
        await orch.dispatch_fan_out(["chat", "ghost"], _req())


async def test_fan_out_settled_span_records_failed_count() -> None:
    orch = _make_orch(FakeLLM(reply="fine"))
    orch.register(_BoomAgent())
    await orch.dispatch_fan_out_settled(["chat", "ghost", "boom"], _req())

    spans = [
        s
        for s in _EXPORTER.get_finished_spans()
        if s.name == "orchestrator.dispatch_fan_out_settled"
    ]
    assert spans, "no finished span named 'orchestrator.dispatch_fan_out_settled'"
    span = spans[-1]
    assert span.attributes is not None
    assert span.attributes["topology"] == "fan_out"
    assert span.attributes["agent_count"] == 3
    assert span.attributes["fan_out.failed_count"] == 2
