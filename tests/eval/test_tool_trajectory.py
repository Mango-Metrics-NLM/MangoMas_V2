"""`tool_trajectory` scorer (spec-0045 B4): recall, extras, missing, step budget, reported apart."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mangomas.errors import ConfigError
from mangomas.eval import scorer_registry
from mangomas.eval.protocol import Scorer, ScorerContext
from mangomas.eval.scorers.tool_trajectory import ToolTrajectoryScorer


def _ctx(expected: list[str], observed: list[str], steps: int | None = None) -> ScorerContext:
    response: dict[str, object] = {"tool_calls": [{"tool": name} for name in observed]}
    if steps is not None:
        response["tool_steps"] = steps
    return ScorerContext(row_metadata={"expected_tools": expected}, response_metadata=response)


def test_satisfies_protocol_and_registers() -> None:
    assert isinstance(ToolTrajectoryScorer(), Scorer)
    assert scorer_registry.get("tool_trajectory")({}).name == "tool_trajectory"


async def test_exact_trajectory_passes_with_full_recall() -> None:
    result = await ToolTrajectoryScorer().score(
        "", "", context=_ctx(["read", "grep"], ["grep", "read"])
    )
    assert (result.passed, result.score) == (True, 1.0)
    assert result.metadata["missing_tools"] == [] and result.metadata["extra_tools"] == []


async def test_a_missing_tool_fails_and_reports_partial_recall() -> None:
    result = await ToolTrajectoryScorer().score("", "", context=_ctx(["read", "grep"], ["read"]))
    assert result.passed is False
    assert result.score == 0.0, "a failed row does not read as mostly right"
    assert result.metadata["recall"] == 0.5
    assert result.metadata["missing_tools"] == ["grep"]


async def test_an_unexpected_tool_fails_unless_allowed() -> None:
    ctx = _ctx(["read"], ["read", "write_file"])
    strict = await ToolTrajectoryScorer().score("", "", context=ctx)
    assert strict.passed is False and strict.metadata["extra_tools"] == ["write_file"]
    lenient = await ToolTrajectoryScorer(allow_extra=True).score("", "", context=ctx)
    assert (lenient.passed, lenient.score) == (True, 1.0)


async def test_no_expected_tools_means_no_tool_may_run() -> None:
    clean = await ToolTrajectoryScorer().score("", "", context=_ctx([], []))
    assert (clean.passed, clean.score) == (True, 1.0)
    dirty = await ToolTrajectoryScorer().score("", "", context=_ctx([], ["write_file"]))
    assert dirty.passed is False


async def test_step_budget_is_enforced_only_when_set() -> None:
    over = await ToolTrajectoryScorer(max_steps=2).score(
        "", "", context=_ctx(["read"], ["read"], 3)
    )
    assert over.passed is False and over.metadata["steps"] == 3
    within = await ToolTrajectoryScorer(max_steps=3).score(
        "", "", context=_ctx(["read"], ["read"], 3)
    )
    assert within.passed is True
    unknown = await ToolTrajectoryScorer(max_steps=3).score(
        "", "", context=_ctx(["read"], ["read"])
    )
    assert unknown.passed is False, "a budget with no reported step count cannot be satisfied"
    unbudgeted = await ToolTrajectoryScorer().score("", "", context=_ctx(["read"], ["read"]))
    assert unbudgeted.passed is True


async def test_missing_context_or_malformed_trace_reads_as_no_calls() -> None:
    result = await ToolTrajectoryScorer().score("", "", context=None)
    assert result.passed is True
    ctx = ScorerContext(
        row_metadata={"expected_tools": "read"}, response_metadata={"tool_calls": "garbage"}
    )
    assert (await ToolTrajectoryScorer().score("", "", context=ctx)).metadata[
        "observed_tools"
    ] == []
    # Entries that are neither a name nor a `{"tool": ...}` record are ignored, not fatal.
    junk = ScorerContext(
        row_metadata={"expected_tools": ["read", 7]},
        response_metadata={"tool_calls": [{"nope": 1}, 3, {"tool": "read"}]},
    )
    graded = await ToolTrajectoryScorer().score("", "", context=junk)
    assert graded.metadata["observed_tools"] == ["read"]
    assert graded.metadata["expected_tools"] == ["read"]


def test_factory_validates_options() -> None:
    with pytest.raises(ConfigError, match="allow_extra"):
        scorer_registry.get("tool_trajectory")({"allow_extra": "yes"})
    with pytest.raises(ConfigError, match="max_steps"):
        scorer_registry.get("tool_trajectory")({"max_steps": 1.5})
    with pytest.raises(ConfigError, match="max_steps"):
        ToolTrajectoryScorer(max_steps=0)


@given(
    expected=st.lists(st.sampled_from(["a", "b", "c", "d"]), unique=True),
    observed=st.lists(st.sampled_from(["a", "b", "c", "d", "e"])),
)
async def test_recall_is_the_covered_fraction_and_pass_implies_full_recall(
    expected: list[str], observed: list[str]
) -> None:
    result = await ToolTrajectoryScorer(allow_extra=True).score(
        "", "", context=_ctx(expected, observed)
    )
    covered = len(set(expected) & set(observed))
    want = 1.0 if not expected else covered / len(expected)
    assert result.metadata["recall"] == pytest.approx(want)
    assert result.passed == (want == 1.0)
