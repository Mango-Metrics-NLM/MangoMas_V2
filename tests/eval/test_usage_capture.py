"""Measured usage flows adapter → target → row → cost (spec-0045 B3), end to end over fakes."""

from __future__ import annotations

from mangomas.agents import ChatAgent
from mangomas.core import AgentContext, AgentRequest, Message, Orchestrator
from mangomas.eval import EvalRunner
from mangomas.eval.dataset import DatasetRow
from mangomas.eval.protocol import ScorerContext, ScoreResult
from mangomas.eval.scorers.cost_budget import CostBudgetScorer
from mangomas.eval.targets import AgentTarget, EchoTarget, FanOutTarget, PipelineTarget
from tests.constants import (
    DEFAULT_AGENT_NAME,
    DEFAULT_EVAL_COST_USD_PER_1K_INPUT_TOKENS,
    DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_TOKENS,
    EVAL_COST_INPUT_TOKENS_METADATA_KEY,
    EVAL_COST_OUTPUT_TOKENS_METADATA_KEY,
    EVAL_USAGE_LLM_CALLS_KEY,
    EVAL_USAGE_METADATA_KEY,
)
from tests.fakes import FakeLLM, FakeRepository

_USAGE = {"prompt_tokens": 100, "completion_tokens": 50}
_REQUEST = AgentRequest(messages=[Message(role="user", content="hello")])
_ROW = DatasetRow(id="r1", messages=list(_REQUEST.messages), expected="anything")


def _orchestrator(llm: FakeLLM) -> Orchestrator:
    orch = Orchestrator(AgentContext(llm=llm, repo=FakeRepository()))
    orch.register(ChatAgent())
    return orch


def _usage(metadata: dict[str, object]) -> dict[str, object]:
    block = metadata[EVAL_USAGE_METADATA_KEY]
    assert isinstance(block, dict)
    return block


async def test_agent_target_carries_the_measured_usage() -> None:
    outcome = await AgentTarget(agent=DEFAULT_AGENT_NAME).run(
        _REQUEST, orch=_orchestrator(FakeLLM(usage=_USAGE))
    )
    assert _usage(outcome.metadata) == {
        EVAL_COST_INPUT_TOKENS_METADATA_KEY: 100,
        EVAL_COST_OUTPUT_TOKENS_METADATA_KEY: 50,
        EVAL_USAGE_LLM_CALLS_KEY: 1,
    }


async def test_a_silent_provider_shows_a_call_with_no_tokens() -> None:
    outcome = await AgentTarget(agent=DEFAULT_AGENT_NAME).run(
        _REQUEST, orch=_orchestrator(FakeLLM(usage=None))
    )
    assert _usage(outcome.metadata)[EVAL_USAGE_LLM_CALLS_KEY] == 1
    assert _usage(outcome.metadata)[EVAL_COST_INPUT_TOKENS_METADATA_KEY] == 0


async def test_echo_target_reports_no_calls_at_all() -> None:
    outcome = await EchoTarget().run(_REQUEST, orch=_orchestrator(FakeLLM(usage=_USAGE)))
    # Echo never dispatches, so it attaches nothing: a reader sees *absence*,
    # not a zero-call block it might mistake for a measured free run.
    assert EVAL_USAGE_METADATA_KEY not in outcome.metadata


async def test_fan_out_sums_every_branch_and_pipeline_sums_every_stage() -> None:
    orch = _orchestrator(FakeLLM(usage=_USAGE))
    fan_out = await FanOutTarget([DEFAULT_AGENT_NAME, DEFAULT_AGENT_NAME]).run(_REQUEST, orch=orch)
    assert _usage(fan_out.metadata)[EVAL_USAGE_LLM_CALLS_KEY] == 2
    assert _usage(fan_out.metadata)[EVAL_COST_INPUT_TOKENS_METADATA_KEY] == 200
    pipeline = await PipelineTarget(agents=[DEFAULT_AGENT_NAME, DEFAULT_AGENT_NAME]).run(
        _REQUEST, orch=orch
    )
    assert _usage(pipeline.metadata)[EVAL_USAGE_LLM_CALLS_KEY] == 2


class _LLMCallingScorer:
    """A judge-shaped scorer: it spends LLM calls of its own."""

    name = "spending"

    async def score(
        self,
        prediction: str,  # noqa: ARG002 — the call, not the text, is under test
        expected: str,  # noqa: ARG002
        *,
        context: ScorerContext | None = None,
    ) -> ScoreResult:
        assert context is not None and context.llm is not None
        await context.llm.complete([Message(role="user", content="judge this")])
        return ScoreResult(score=1.0, passed=True)


async def test_scorer_calls_are_not_billed_to_the_row() -> None:
    """The target closes its scope before the scorer runs (the judge-pollution counter-argument)."""
    report = await EvalRunner(_orchestrator(FakeLLM(usage=_USAGE)), _LLMCallingScorer()).run(
        [_ROW], target=AgentTarget(agent=DEFAULT_AGENT_NAME)
    )
    assert _usage(report.rows[0].response_metadata)[EVAL_USAGE_LLM_CALLS_KEY] == 1


async def test_cost_budget_prices_the_measured_tokens_end_to_end() -> None:
    report = await EvalRunner(_orchestrator(FakeLLM(usage=_USAGE)), CostBudgetScorer()).run(
        [_ROW], target=AgentTarget(agent=DEFAULT_AGENT_NAME)
    )
    expected = (
        100 / 1000 * DEFAULT_EVAL_COST_USD_PER_1K_INPUT_TOKENS
        + 50 / 1000 * DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_TOKENS
    )
    assert report.mean_cost_usd is not None
    assert abs(report.mean_cost_usd - expected) < 1e-12
    assert report.rows[0].metadata["source"] == "measured_tokens"
