"""The measured-outcome seam (spec-0045 B2): what a target produces reaches the scorer.

Four properties, each the inverse of a limit `test_cost_measurement_basis.py`
used to pin:

* the runner stores the target's response metadata and the row's correlation id
  on `EvalRowResult`;
* the scorer receives the row's input messages and the response metadata;
* a third-party target still returning the pre-spec ``str`` is shimmed with a
  WARNING, and anything else is a loud error (an errored row, never an empty
  prediction);
* a baseline written before these fields existed still loads.
"""

from __future__ import annotations

import logging
from typing import cast

import pytest

from mangomas.core import AgentRequest, Message, Orchestrator
from mangomas.eval import EvalRunner
from mangomas.eval.baseline import _row_from_dict
from mangomas.eval.dataset import DatasetRow
from mangomas.eval.protocol import ScorerContext, ScoreResult
from mangomas.eval.target import Target, TargetOutcome, coerce_outcome
from mangomas.eval.targets import AgentTarget
from tests.constants import DEFAULT_AGENT_NAME

_ROW = DatasetRow(
    id="r1",
    messages=[Message(role="user", content="what is the seam?")],
    expected="anything",
    metadata={"category": "seam"},
)


class _RecordingScorer:
    """Keeps every ScorerContext it was handed."""

    name = "recording"

    def __init__(self) -> None:
        self.contexts: list[ScorerContext] = []

    async def score(
        self,
        prediction: str,  # noqa: ARG002 — the context is what this scorer records
        expected: str,  # noqa: ARG002
        *,
        context: ScorerContext | None = None,
    ) -> ScoreResult:
        assert context is not None
        self.contexts.append(context)
        return ScoreResult(score=1.0, passed=True)


class _LegacyStrTarget:
    name = "legacy"

    async def run(
        self,
        request: AgentRequest,  # noqa: ARG002 — the pre-spec contract, fixed reply
        *,
        orch: Orchestrator,  # noqa: ARG002
    ) -> str:
        return "legacy text"


class _BrokenTarget:
    name = "broken"

    async def run(
        self,
        request: AgentRequest,  # noqa: ARG002 — returns neither str nor outcome
        *,
        orch: Orchestrator,  # noqa: ARG002
    ) -> None:
        return None


async def test_runner_stores_response_metadata_and_correlation_id(
    eval_orchestrator: Orchestrator,
) -> None:
    scorer = _RecordingScorer()
    report = await EvalRunner(eval_orchestrator, scorer).run(
        [_ROW], target=AgentTarget(agent=DEFAULT_AGENT_NAME)
    )
    row = report.rows[0]
    assert row.correlation_id
    assert "loop" in row.response_metadata, "the orchestrator's loop counters travel with the row"
    assert scorer.contexts[0].correlation_id == row.correlation_id


async def test_scorer_receives_the_input_and_the_response_metadata(
    eval_orchestrator: Orchestrator,
) -> None:
    scorer = _RecordingScorer()
    await EvalRunner(eval_orchestrator, scorer).run(
        [_ROW], target=AgentTarget(agent=DEFAULT_AGENT_NAME)
    )
    ctx = scorer.contexts[0]
    assert ctx.messages == _ROW.messages
    assert ctx.row_metadata == {"category": "seam"}
    assert "loop" in ctx.response_metadata
    # The two mappings are distinct: declared vs produced.
    assert "category" not in ctx.response_metadata


async def test_legacy_str_target_is_shimmed_with_a_warning(
    eval_orchestrator: Orchestrator, caplog: pytest.LogCaptureFixture
) -> None:
    target = _LegacyStrTarget()
    assert isinstance(target, Target), "runtime_checkable sees the method, not its return type"
    with caplog.at_level(logging.WARNING, logger="mangomas.eval.target"):
        report = await EvalRunner(eval_orchestrator, _RecordingScorer()).run([_ROW], target=target)
    assert report.rows[0].prediction == "legacy text"
    assert report.rows[0].response_metadata == {}
    assert any(r.__dict__.get("event") == "eval_target_legacy_str" for r in caplog.records)


async def test_a_target_returning_neither_is_an_errored_row(
    eval_orchestrator: Orchestrator,
) -> None:
    # The cast is the point: a plugin can violate the contract at runtime.
    report = await EvalRunner(eval_orchestrator, _RecordingScorer()).run(
        [_ROW], target=cast("Target", _BrokenTarget())
    )
    assert report.errored == 1
    assert report.rows[0].metadata == {"error_type": "TypeError"}
    assert report.rows[0].correlation_id


def test_coerce_outcome_passes_an_outcome_through_unchanged() -> None:
    outcome = TargetOutcome(content="x", metadata={"k": 1})
    assert coerce_outcome(outcome, target_name="t") is outcome
    with pytest.raises(TypeError, match="expected TargetOutcome"):
        coerce_outcome(42, target_name="t")


def test_a_pre_spec_baseline_row_still_loads() -> None:
    legacy = {
        "row_id": "r1",
        "score": 1.0,
        "passed": True,
        "duration_ms": 1.5,
        "prediction": "p",
        "expected": "p",
        "metadata": {},
        "error": None,
    }
    row = _row_from_dict(legacy)
    assert row.correlation_id is None
    assert row.response_metadata == {}
