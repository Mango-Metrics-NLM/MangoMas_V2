"""Tests for the EvalRunner."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from mangomas.core import Orchestrator
from mangomas.eval import EvalRunner, load_jsonl
from mangomas.eval.scorers.exact_match import ExactMatchScorer
from tests.constants import STUB_REPLY


async def test_runner_empty_dataset_returns_zero_report(
    eval_orchestrator: Orchestrator,
) -> None:
    runner = EvalRunner(eval_orchestrator, ExactMatchScorer())
    report = await runner.run([], agent_name="chat")
    assert report.dataset_size == 0
    assert report.passed == 0
    assert report.failed == 0
    assert report.rows == []


async def test_runner_all_pass(eval_orchestrator: Orchestrator, fixtures_dir: Path) -> None:
    """FakeLLM echoes STUB_REPLY; all_pass.jsonl expects exactly that."""
    rows = await load_jsonl(fixtures_dir / "all_pass.jsonl")
    assert all(row.expected.lower() == STUB_REPLY.lower() for row in rows)
    runner = EvalRunner(eval_orchestrator, ExactMatchScorer())
    report = await runner.run(rows, agent_name="chat")
    assert report.dataset_size == 2
    assert report.passed == 2
    assert report.failed == 0
    assert report.mean_score == pytest.approx(1.0)


async def test_runner_all_fail(eval_orchestrator: Orchestrator, fixtures_dir: Path) -> None:
    rows = await load_jsonl(fixtures_dir / "all_fail.jsonl")
    runner = EvalRunner(eval_orchestrator, ExactMatchScorer())
    report = await runner.run(rows, agent_name="chat")
    assert report.passed == 0
    assert report.failed == 2
    assert report.mean_score == pytest.approx(0.0)


async def test_runner_mixed_dataset(eval_orchestrator: Orchestrator, fixtures_dir: Path) -> None:
    rows = await load_jsonl(fixtures_dir / "mixed.jsonl")
    runner = EvalRunner(eval_orchestrator, ExactMatchScorer())
    report = await runner.run(rows, agent_name="chat")
    assert report.dataset_size == 3
    assert report.passed == 2
    assert report.failed == 1


async def test_runner_logs_summary(
    eval_orchestrator: Orchestrator,
    fixtures_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    rows = await load_jsonl(fixtures_dir / "mixed.jsonl")
    runner = EvalRunner(eval_orchestrator, ExactMatchScorer())
    with caplog.at_level(logging.INFO, logger="mangomas.eval.runner"):
        await runner.run(rows, agent_name="chat")
    summaries = [rec for rec in caplog.records if getattr(rec, "event", None) == "eval_summary"]
    assert summaries, "expected an eval_summary INFO record"
    assert getattr(summaries[0], "dataset_size", None) == 3


async def test_runner_concurrent_execution_preserves_counts(
    eval_orchestrator: Orchestrator, fixtures_dir: Path
) -> None:
    rows = await load_jsonl(fixtures_dir / "mixed.jsonl")
    runner = EvalRunner(eval_orchestrator, ExactMatchScorer(), parallelism=3)
    report = await runner.run(rows, agent_name="chat")
    assert report.dataset_size == 3
    assert report.passed + report.failed + report.errored == 3


async def test_runner_fail_fast_short_circuits(
    eval_orchestrator: Orchestrator, fixtures_dir: Path
) -> None:
    rows = await load_jsonl(fixtures_dir / "all_fail.jsonl")
    runner = EvalRunner(eval_orchestrator, ExactMatchScorer(), parallelism=1, fail_fast=True)
    report = await runner.run(rows, agent_name="chat")
    # With parallelism=1 and fail_fast, every row after the first failure is
    # cancelled — exactly one row runs, the rest are recorded as cancelled.
    cancelled = [r for r in report.rows if r.error == "cancelled by fail_fast"]
    assert cancelled, "expected at least one cancelled row"


async def test_runner_records_scorer_error(eval_orchestrator: Orchestrator) -> None:
    """A scorer that raises gets a structured error row, not a crash."""

    class _Boom:
        name = "boom"

        async def score(self, *_: object, **__: object) -> object:
            raise RuntimeError("scorer exploded")

    from mangomas.core.agent import Message  # noqa: PLC0415
    from mangomas.eval.dataset import DatasetRow  # noqa: PLC0415

    runner = EvalRunner(eval_orchestrator, _Boom())  # type: ignore[arg-type]
    rows = [
        DatasetRow(
            id="x",
            messages=[Message(role="user", content="x")],
            expected="y",
        )
    ]
    report = await runner.run(rows, agent_name="chat")
    assert report.errored == 1
    assert report.rows[0].error is not None
    assert "exploded" in report.rows[0].error


def test_runner_rejects_invalid_parallelism(eval_orchestrator: Orchestrator) -> None:
    with pytest.raises(ValueError):
        EvalRunner(eval_orchestrator, ExactMatchScorer(), parallelism=0)
