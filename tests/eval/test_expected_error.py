"""`DatasetRow.expected_error`: a row that must refuse (spec-0045 B4).

Before this field a refusal was an *errored* row, so "the agent must reject
this" was unexpressible. Both directions are pinned: the named refusal passes
the row; an answer where a refusal was required fails it without consulting
the scorer; a *different* error is still an error.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mangomas.agents import ChatAgent
from mangomas.core import AgentContext, AgentRequest, Message, Orchestrator
from mangomas.errors import ToolExecutionError
from mangomas.eval import EvalRunner
from mangomas.eval.dataset import DatasetError, DatasetRow, load_jsonl
from mangomas.eval.protocol import ScorerContext, ScoreResult
from mangomas.eval.runner import EXPECTED_ERROR_METADATA_KEY, REFUSED_METADATA_KEY
from mangomas.eval.scorers.exact_match import ExactMatchScorer
from mangomas.eval.target import TargetOutcome
from tests.constants import DEFAULT_AGENT_NAME
from tests.fakes import FakeLLM, FakeRepository

_MESSAGES = [Message(role="user", content="do the forbidden thing")]


class _RefusingTarget:
    name = "refusing"

    def __init__(self, exc: Exception | None) -> None:
        self._exc = exc

    async def run(self, request: AgentRequest, *, orch: Orchestrator) -> TargetOutcome:  # noqa: ARG002
        if self._exc is not None:
            raise self._exc
        return TargetOutcome(content="sure, done")


class _NeverCalledScorer:
    name = "never"

    async def score(
        self,
        prediction: str,  # noqa: ARG002 — must never be reached
        expected: str,  # noqa: ARG002
        *,
        context: ScorerContext | None = None,  # noqa: ARG002
    ) -> ScoreResult:
        raise AssertionError("the scorer must not be consulted on a must-refuse row")


def _orchestrator() -> Orchestrator:
    orch = Orchestrator(AgentContext(llm=FakeLLM(), repo=FakeRepository()))
    orch.register(ChatAgent())
    return orch


def _row(expected_error: str | None) -> DatasetRow:
    return DatasetRow(id="r1", messages=_MESSAGES, expected="", expected_error=expected_error)


async def test_the_named_refusal_passes_the_row_by_class_name() -> None:
    report = await EvalRunner(_orchestrator(), _NeverCalledScorer()).run(
        [_row("ToolExecutionError")],
        target=_RefusingTarget(ToolExecutionError("blocked", tool_name="write_file")),
    )
    row = report.rows[0]
    assert (row.passed, row.score, row.error) == (True, 1.0, None)
    assert row.metadata[EXPECTED_ERROR_METADATA_KEY] == "ToolExecutionError"
    assert row.metadata[REFUSED_METADATA_KEY] is True
    assert report.passed == 1 and report.errored == 0


async def test_the_named_refusal_also_matches_the_error_code() -> None:
    report = await EvalRunner(_orchestrator(), _NeverCalledScorer()).run(
        [_row("tool_execution_error")],
        target=_RefusingTarget(ToolExecutionError("blocked", tool_name="write_file")),
    )
    assert report.rows[0].passed is True


async def test_an_answer_where_a_refusal_was_required_fails_without_scoring() -> None:
    report = await EvalRunner(_orchestrator(), _NeverCalledScorer()).run(
        [_row("ToolExecutionError")], target=_RefusingTarget(None)
    )
    row = report.rows[0]
    assert (row.passed, row.score, row.error) == (False, 0.0, None)
    assert row.prediction == "sure, done"
    assert row.metadata[REFUSED_METADATA_KEY] is False
    assert report.failed == 1


async def test_a_different_error_is_still_an_error() -> None:
    report = await EvalRunner(_orchestrator(), _NeverCalledScorer()).run(
        [_row("ToolExecutionError")], target=_RefusingTarget(RuntimeError("unrelated"))
    )
    assert report.errored == 1
    assert report.rows[0].metadata == {"error_type": "RuntimeError"}


async def test_rows_without_expected_error_are_untouched() -> None:
    report = await EvalRunner(_orchestrator(), ExactMatchScorer()).run(
        [DatasetRow(id="r1", messages=_MESSAGES, expected="sure, done")],
        target=_RefusingTarget(None),
    )
    assert report.rows[0].passed is True


async def test_expected_error_is_parsed_and_validated(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "must-refuse",
                "messages": [{"role": "user", "content": "x"}],
                "expected": "",
                "expected_error": "ToolExecutionError",
            }
        )
        + "\n"
        + json.dumps(
            {"id": "plain", "messages": [{"role": "user", "content": "x"}], "expected": "y"}
        )
        + "\n",
        encoding="utf-8",
    )
    rows = await load_jsonl(path)
    assert [r.expected_error for r in rows] == ["ToolExecutionError", None]

    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        json.dumps(
            {"messages": [{"role": "user", "content": "x"}], "expected": "", "expected_error": ""}
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(DatasetError, match="expected_error"):
        await load_jsonl(bad)
    assert DEFAULT_AGENT_NAME  # the orchestrator fixture agent exists; keeps the import honest
