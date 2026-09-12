"""Cost-controlled eval dataset: n>5, echo / agent / pipeline share budget."""

from __future__ import annotations

from pathlib import Path

import pytest

from mangomas.core import Orchestrator
from mangomas.eval import EvalRunner, load_jsonl
from mangomas.eval.scorers.cost_budget import CostBudgetScorer
from mangomas.eval.targets import AgentTarget, EchoTarget, PipelineTarget
from tests.constants import (
    DEFAULT_AGENT_NAME,
    DEFAULT_EVAL_COST_USD_PER_1K_INPUT_TOKENS,
    DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_TOKENS,
    EVAL_COST_CONTROLLED_DATASET_FILENAME,
    EVAL_COST_CONTROLLED_MIN_ROWS,
    EVAL_COST_INPUT_TOKENS_METADATA_KEY,
    EVAL_COST_OUTPUT_TOKENS_METADATA_KEY,
    EVAL_COST_SOURCE_TOKENS,
    EVAL_COST_TOKENS_PER_THOUSAND,
    EVAL_COST_USD_METADATA_KEY,
    STUB_REPLY,
)

_CHAT_PIPELINE = [DEFAULT_AGENT_NAME]


def _token_cost(input_tokens: float, output_tokens: float) -> float:
    return (
        input_tokens / EVAL_COST_TOKENS_PER_THOUSAND * DEFAULT_EVAL_COST_USD_PER_1K_INPUT_TOKENS
        + output_tokens / EVAL_COST_TOKENS_PER_THOUSAND * DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_TOKENS
    )


def _require_number(value: object) -> float:
    if isinstance(value, bool):
        raise TypeError("expected a number, got bool")
    if isinstance(value, int | float):
        return float(value)
    raise TypeError(f"expected a number, got {type(value).__name__}")


async def test_cost_controlled_dataset_exceeds_smoke_n(
    fixtures_dir: Path,
) -> None:
    rows = await load_jsonl(fixtures_dir / EVAL_COST_CONTROLLED_DATASET_FILENAME)
    assert len(rows) >= EVAL_COST_CONTROLLED_MIN_ROWS
    assert all(row.expected == STUB_REPLY for row in rows)
    assert all(row.messages[-1].content == STUB_REPLY for row in rows)


async def test_echo_agent_pipeline_share_mean_cost(
    eval_orchestrator: Orchestrator,
    fixtures_dir: Path,
) -> None:
    """Hold model/tools/budget constant: same rows, same FakeLLM, three targets."""
    rows = await load_jsonl(fixtures_dir / EVAL_COST_CONTROLLED_DATASET_FILENAME)
    scorer = CostBudgetScorer()
    runner = EvalRunner(eval_orchestrator, scorer)
    echo = await runner.run(rows, target=EchoTarget())
    agent = await runner.run(rows, target=AgentTarget(agent=DEFAULT_AGENT_NAME))
    pipeline = await runner.run(rows, target=PipelineTarget(agents=list(_CHAT_PIPELINE)))

    expected_costs = [
        _token_cost(
            _require_number(row.metadata[EVAL_COST_INPUT_TOKENS_METADATA_KEY]),
            _require_number(row.metadata[EVAL_COST_OUTPUT_TOKENS_METADATA_KEY]),
        )
        for row in rows
    ]
    expected_mean = sum(expected_costs) / len(expected_costs)
    echo_costs = [_require_number(row.metadata[EVAL_COST_USD_METADATA_KEY]) for row in echo.rows]

    assert echo.mean_cost_usd == pytest.approx(expected_mean)
    assert echo.mean_cost_usd == pytest.approx(sum(echo_costs) / len(echo_costs))
    assert echo.mean_cost_usd == pytest.approx(agent.mean_cost_usd)
    assert agent.mean_cost_usd == pytest.approx(pipeline.mean_cost_usd)
    assert echo.passed == len(rows)
    assert agent.passed == len(rows)
    assert pipeline.passed == len(rows)
    assert all(row.metadata["source"] == EVAL_COST_SOURCE_TOKENS for row in echo.rows)
    assert echo_costs == pytest.approx(expected_costs)
