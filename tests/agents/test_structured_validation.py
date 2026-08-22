"""Tests for ``StructuredOutputAgent.parse`` and the opt-in ``validate_output`` flag.

Covers the roadmap-1.3 structured-output validation surface: ``parse()`` as the
public schema-validating accessor (``LLMBadResponse`` on malformed output, with
a truncated detail that never carries the full LLM content), and the
``MANGOMAS_AGENTS__<NAME>__VALIDATE_OUTPUT`` flag that makes ``handle`` reject
malformed output while leaving valid output byte-for-byte unchanged.
"""

from __future__ import annotations

import logging

import pytest

from mangomas.agents.planner import ExecutionPlan, PlannerAgent, PlanStep
from mangomas.agents.reviewer import ReviewerAgent, ReviewResult
from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE, AgentSettings
from mangomas.core.agent import AgentContext, AgentRequest, Message
from mangomas.errors import LLMBadResponse
from tests.fakes import FakeLLM


def _plan_json() -> str:
    return ExecutionPlan(
        goal="ship it",
        steps=[PlanStep(step=1, description="Build", agent="tool")],
    ).model_dump_json()


def _review_json() -> str:
    return ReviewResult(passed=True, score=0.9, feedback="LGTM").model_dump_json()


def _req(content: str = "go") -> AgentRequest:
    return AgentRequest(messages=[Message(role="user", content=content)])


# ── parse(): success paths ────────────────────────────────────────────────────


def test_planner_parse_returns_execution_plan() -> None:
    parsed = PlannerAgent().parse(_plan_json())
    assert isinstance(parsed, ExecutionPlan)
    assert parsed.goal == "ship it"


def test_reviewer_parse_returns_review_result() -> None:
    parsed = ReviewerAgent().parse(_review_json())
    assert isinstance(parsed, ReviewResult)
    assert parsed.passed is True


# ── parse(): failure paths ────────────────────────────────────────────────────


def test_parse_invalid_json_raises_llm_bad_response() -> None:
    with pytest.raises(LLMBadResponse):
        PlannerAgent().parse("not json at all")


def test_parse_schema_mismatch_raises_llm_bad_response() -> None:
    # Valid JSON, wrong shape: ExecutionPlan requires at least one step.
    with pytest.raises(LLMBadResponse):
        PlannerAgent().parse('{"goal": "x", "steps": []}')


def test_parse_error_names_agent_and_schema_not_content() -> None:
    marker = "SECRET-CONTENT-MARKER"
    with pytest.raises(LLMBadResponse) as excinfo:
        ReviewerAgent().parse(f'{{"unexpected": "{marker}"')
    message = str(excinfo.value)
    assert "reviewer" in message
    assert "ReviewResult" in message
    assert marker not in message


def test_parse_detail_is_truncated() -> None:
    """The detail is bounded by ``DEFAULT_ERROR_DETAIL_TRUNCATE`` and never
    carries the full LLM content."""
    content = "x" * (DEFAULT_ERROR_DETAIL_TRUNCATE * 3)
    with pytest.raises(LLMBadResponse) as excinfo:
        PlannerAgent().parse(content)
    assert len(excinfo.value.detail) <= DEFAULT_ERROR_DETAIL_TRUNCATE
    assert content not in str(excinfo.value)
    assert content not in excinfo.value.detail


def test_parse_failure_logs_error_without_full_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The pre-raise log record is ERROR level and carries only length + a
    truncated head of the content."""
    content = "y" * (DEFAULT_ERROR_DETAIL_TRUNCATE * 3)
    with (
        caplog.at_level(logging.ERROR, logger="mangomas.agents._structured"),
        pytest.raises(LLMBadResponse),
    ):
        PlannerAgent().parse(content)
    records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert records, "expected an ERROR record before the raise"
    assert content not in caplog.text
    assert str(len(content)) in caplog.text


# ── handle(): the validate_output flag ────────────────────────────────────────


async def test_handle_validate_on_rejects_malformed_output() -> None:
    llm = FakeLLM(reply="definitely not the schema")
    ctx = AgentContext(llm=llm, repo=None)
    agent = PlannerAgent(settings=AgentSettings(validate_output=True))
    with pytest.raises(LLMBadResponse):
        await agent.handle(_req(), ctx)


async def test_handle_validate_on_passes_valid_output_unchanged() -> None:
    plan_json = _plan_json()
    llm = FakeLLM(reply=plan_json)
    ctx = AgentContext(llm=llm, repo=None)
    agent = PlannerAgent(settings=AgentSettings(validate_output=True))
    resp = await agent.handle(_req(), ctx)
    # Byte-for-byte the raw LLM content — the parsed object is discarded.
    assert resp.content == plan_json
    assert resp.agent == "planner"


async def test_handle_default_flag_off_returns_raw_content() -> None:
    """Backwards-compat proof: the default AgentSettings leave validation off,
    so malformed content passes through unchanged."""
    llm = FakeLLM(reply="not json")
    ctx = AgentContext(llm=llm, repo=None)
    agent = ReviewerAgent(settings=AgentSettings())
    resp = await agent.handle(_req(), ctx)
    assert resp.content == "not json"


async def test_handle_no_settings_returns_raw_content() -> None:
    """``settings=None`` (the composition default for an unconfigured agent)
    also leaves validation off."""
    llm = FakeLLM(reply="not json")
    ctx = AgentContext(llm=llm, repo=None)
    resp = await PlannerAgent().handle(_req(), ctx)
    assert resp.content == "not json"


async def test_handle_validate_on_reviewer_rejects_malformed_output() -> None:
    llm = FakeLLM(reply='{"passed": "not-a-bool"}')
    ctx = AgentContext(llm=llm, repo=None)
    agent = ReviewerAgent(settings=AgentSettings(validate_output=True))
    with pytest.raises(LLMBadResponse):
        await agent.handle(_req(), ctx)
