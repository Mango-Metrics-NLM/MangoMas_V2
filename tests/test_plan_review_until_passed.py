"""The shipped acceptance-gated example graph.

``examples/workflows/plan-execute-review.json`` is the all-``agent`` graph whose
equality with ``dispatch_pipeline`` is the parity proof
(``tests/test_plan_execute_review.py``). It therefore demonstrates no
acceptance at all — which is how three consecutive reviews came to describe it as
"terminating when the reviewer says something matching a substring", a mechanism
it does not contain.

This module covers the sibling example that *does* demonstrate acceptance:
``plan-review-until-passed.json`` gates its reviewer step on
``ReviewResult.passed`` via a ``json_field`` predicate. The tests below pin the
three properties a reader copies it for — it is valid under the structured
guard, it terminates on an approving review, and it does **not** terminate on a
rejecting one whose prose contains the words a substring match would have caught.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mangomas.agents import PlannerAgent, ReviewerAgent, ToolAgent
from mangomas.agents.planner import ExecutionPlan, PlanStep
from mangomas.agents.reviewer import ReviewResult
from mangomas.composition.agents import STRUCTURED_AGENT_FIELDS
from mangomas.config import AgentSettings
from mangomas.core import AgentRequest, Message, Orchestrator
from mangomas.core.agent import AgentContext
from mangomas.errors import MaxStepsExceeded
from mangomas.workflow import execute_workflow, load_workflow
from mangomas.workflow.graph import LoopNode, SequenceNode
from mangomas.workflow.predicate import KIND_JSON_FIELD
from tests.constants import (
    PLAN_EXECUTE_REVIEW_AGENTS,
    PLAN_REVIEW_UNTIL_PASSED_GRAPH_NAME,
    PLAN_REVIEW_UNTIL_PASSED_GRAPH_RELPATH,
    PLAN_REVIEW_UNTIL_PASSED_MAX_STEPS,
    REVIEW_PASSED_FIELD,
)
from tests.fakes import FakeLLM

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GRAPH_PATH = _REPO_ROOT / PLAN_REVIEW_UNTIL_PASSED_GRAPH_RELPATH

# Serialised from the real models, never hand-written JSON: these agents run with
# ``validate_output=True`` below, so a fixture that drifts from the schema would
# fail as an ``LLMBadResponse`` and read like a product bug.
_PLAN_JSON = ExecutionPlan(
    goal="ship the release",
    steps=[PlanStep(step=1, description="Build the artefact", agent="tool")],
).model_dump_json()
_TOOL_REPLY = "Build completed; artefact published."
#: An approving review — the loop must accept on this.
_APPROVING_REVIEW = ReviewResult(passed=True, score=0.9, feedback="LGTM").model_dump_json()
#: A *rejecting* review whose prose carries the needle a ``contains: approved``
#: predicate would have matched. This is the spec-0032 false positive, kept here
#: as the behavioural counterpart to the load-time guard.
_REJECTING_REVIEW = ReviewResult(
    passed=False,
    score=0.2,
    feedback="the approved approach was rejected",
    suggestions=["rework"],
).model_dump_json()


def _request() -> AgentRequest:
    return AgentRequest(messages=[Message(role="user", content="review my plan")])


def _orchestrator(*replies: str) -> Orchestrator:
    """The canonical roster over a scripted ``FakeLLM``, validation ON.

    Mirrors ``test_plan_execute_review.py::_validated_orch`` so the two examples
    are exercised against an identical runtime — any behavioural difference
    between them is then the graph's, which is the whole point of having both.
    """
    ctx = AgentContext(llm=FakeLLM(replies=list(replies)), repo=None)
    orch = Orchestrator(ctx)
    validating = AgentSettings(validate_output=True)
    orch.register(PlannerAgent(settings=validating))
    orch.register(ToolAgent())
    orch.register(ReviewerAgent(settings=validating))
    return orch


# ── the file itself ───────────────────────────────────────────────────────────


def test_example_graph_file_exists() -> None:
    assert _GRAPH_PATH.is_file()


def test_example_graph_loads_under_the_structured_guard() -> None:
    """The shipped example must satisfy the rules `load_workflow` now enforces.

    A shipped example that the product's own validator refuses would be worse
    than no example, so this is the load-bearing assertion of the module.
    """
    graph = load_workflow(str(_GRAPH_PATH), structured_agents=STRUCTURED_AGENT_FIELDS)
    assert graph.name == PLAN_REVIEW_UNTIL_PASSED_GRAPH_NAME


def test_example_graph_gates_on_a_parsed_field() -> None:
    """The reviewer step is a ``loop`` accepting on a ``json_field`` predicate.

    Red before this example existed: the only shipped graph was a bare
    three-step ``sequence`` with no predicate anywhere.
    """
    graph = load_workflow(str(_GRAPH_PATH), structured_agents=STRUCTURED_AGENT_FIELDS)
    assert isinstance(graph.root, SequenceNode)
    final = graph.root.steps[-1]
    assert isinstance(final, LoopNode)
    assert final.accept.kind == KIND_JSON_FIELD
    assert final.accept.field == REVIEW_PASSED_FIELD
    assert final.accept.equals is True
    assert final.max_steps == PLAN_REVIEW_UNTIL_PASSED_MAX_STEPS


def test_example_graph_runs_the_same_agents_as_the_parity_example() -> None:
    """Same roster, one gate added — so the two examples are comparable.

    If they drifted apart, a reader could not tell which difference is the point.
    """
    graph = load_workflow(str(_GRAPH_PATH), structured_agents=STRUCTURED_AGENT_FIELDS)
    assert isinstance(graph.root, SequenceNode)
    agents = tuple(
        step.agent
        for step in graph.root.steps
        if isinstance(step, LoopNode) or hasattr(step, "agent")
    )
    assert agents == PLAN_EXECUTE_REVIEW_AGENTS


# ── behaviour: what the gate actually does ────────────────────────────────────


async def test_an_approving_review_accepts_on_the_first_pass() -> None:
    """``passed: true`` satisfies the predicate, so the loop ends immediately."""
    orch = _orchestrator(_PLAN_JSON, _TOOL_REPLY, _APPROVING_REVIEW)
    graph = load_workflow(str(_GRAPH_PATH), structured_agents=STRUCTURED_AGENT_FIELDS)
    response = await execute_workflow(graph, _request(), orch=orch)
    assert response.agent == "reviewer"
    assert response.content == _APPROVING_REVIEW


async def test_a_rejecting_review_does_not_accept_even_carrying_the_needle() -> None:
    """The behavioural half of the §1.1 defect, pinned.

    ``feedback`` here reads "the approved approach was rejected" — a
    ``contains: approved`` predicate accepts it, terminating the loop on a
    rejecting review. Binding to the parsed ``passed`` field does not, so the
    loop runs out its budget and raises ``MaxStepsExceeded`` instead of
    reporting success. **That the run fails is the correct outcome.**
    """
    orch = _orchestrator(
        _PLAN_JSON,
        _TOOL_REPLY,
        *[_REJECTING_REVIEW] * PLAN_REVIEW_UNTIL_PASSED_MAX_STEPS,
    )
    graph = load_workflow(str(_GRAPH_PATH), structured_agents=STRUCTURED_AGENT_FIELDS)
    with pytest.raises(MaxStepsExceeded):
        await execute_workflow(graph, _request(), orch=orch)


async def test_the_loop_accepts_once_the_review_turns_approving() -> None:
    """Two-sided: a reviewer that converges within budget must succeed.

    Without this, a guard that never accepted would pass the rejection test
    above and look correct.
    """
    orch = _orchestrator(_PLAN_JSON, _TOOL_REPLY, _REJECTING_REVIEW, _APPROVING_REVIEW)
    graph = load_workflow(str(_GRAPH_PATH), structured_agents=STRUCTURED_AGENT_FIELDS)
    response = await execute_workflow(graph, _request(), orch=orch)
    assert response.content == _APPROVING_REVIEW
