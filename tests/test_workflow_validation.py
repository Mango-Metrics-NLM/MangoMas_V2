"""Structured-acceptance validation: a loop may not grade a self-report.

Covers ``mangomas.workflow.validation`` and the ``structured_agents`` keyword
``load_workflow`` grew for it.

Both rules were *loadable defects* before this module: a ``contains`` predicate
over ``reviewer`` accepted a rejecting review, and a misspelled ``json_field``
path compiled to a closure that could never accept. The "loads today" tests below
are therefore the record of what was broken, and the refusal tests are the fix —
each rule is proven in both directions, per ``mango-mutation-proof``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mangomas.composition.agents import STRUCTURED_AGENT_FIELDS
from mangomas.errors import ConfigError
from mangomas.workflow.graph import WorkflowGraph, iter_nodes
from mangomas.workflow.loader import load_workflow
from mangomas.workflow.predicate import PredicateSpec
from mangomas.workflow.validation import (
    StructuredAgentSchema,
    structured_acceptance_problems,
    validate_structured_acceptance,
)
from tests.constants import (
    DEFAULT_AGENT_NAME,
    PLANNER_AGENT_NAME,
    REVIEW_PASSED_FIELD,
    REVIEWER_AGENT_NAME,
    WORKFLOW_SCHEMA_VERSION_CURRENT,
)


def _loop_graph(
    agent: str,
    accept: dict[str, Any],
    *,
    name: str = "acceptance-probe",
    max_steps: int = 3,
) -> str:
    """Return inline JSON for a single-``loop`` graph over *agent*."""
    return json.dumps(
        {
            "schema_version": WORKFLOW_SCHEMA_VERSION_CURRENT,
            "name": name,
            "root": {
                "kind": "loop",
                "agent": agent,
                "max_steps": max_steps,
                "accept": accept,
            },
        }
    )


_TEXT_ACCEPT: dict[str, Any] = {"kind": "contains", "value": "approved"}
_REGEX_ACCEPT: dict[str, Any] = {"kind": "regex", "value": "pass(ed)?"}
_VALID_JSON_ACCEPT: dict[str, Any] = {
    "kind": "json_field",
    "field": REVIEW_PASSED_FIELD,
    "equals": True,
}
_TYPO_JSON_ACCEPT: dict[str, Any] = {"kind": "json_field", "field": "pased", "equals": True}


# ── back-compat: the keyword is opt-in and defaults to off ────────────────────


@pytest.mark.parametrize("accept", [_TEXT_ACCEPT, _REGEX_ACCEPT, _TYPO_JSON_ACCEPT])
def test_every_previously_valid_graph_still_loads_without_the_keyword(
    accept: dict[str, Any],
) -> None:
    """``load_workflow(source)`` is unchanged — this is the back-compat contract.

    The three specs here are exactly the ones the new rules refuse. Loading them
    with no ``structured_agents`` must still succeed, or the additive keyword
    would be a breaking change for every library caller.
    """
    graph = load_workflow(_loop_graph(REVIEWER_AGENT_NAME, accept))
    assert graph.name == "acceptance-probe"


@pytest.mark.parametrize("empty", [None, {}])
def test_an_empty_map_disables_both_rules(empty: dict[str, StructuredAgentSchema] | None) -> None:
    """``None`` and ``{}`` both mean "no structured agents known", not "check all"."""
    graph = load_workflow(_loop_graph(REVIEWER_AGENT_NAME, _TEXT_ACCEPT), structured_agents=empty)
    assert graph.root.kind == "loop"


# ── rule 1: no text predicate over a structured agent ─────────────────────────


@pytest.mark.parametrize("accept", [_TEXT_ACCEPT, _REGEX_ACCEPT])
def test_text_predicate_over_a_structured_agent_is_refused(accept: dict[str, Any]) -> None:
    """A loop over ``reviewer`` may not accept on a substring of its own reply.

    This is the §1.1 defect: ``{"verdict": "reject", "notes": "approved approach
    rejected"}`` satisfies ``contains: approved``, so the loop terminates on a
    rejecting review. ``regex`` is covered too — fixing only ``contains`` would
    leave the same hole one spelling away.
    """
    with pytest.raises(ConfigError, match="self-report"):
        load_workflow(
            _loop_graph(REVIEWER_AGENT_NAME, accept),
            structured_agents=STRUCTURED_AGENT_FIELDS,
        )


def test_the_refusal_names_the_supported_alternative() -> None:
    """The error must say what to do instead, not only that this is wrong."""
    with pytest.raises(ConfigError) as excinfo:
        load_workflow(
            _loop_graph(REVIEWER_AGENT_NAME, _TEXT_ACCEPT),
            structured_agents=STRUCTURED_AGENT_FIELDS,
        )
    assert "json_field" in str(excinfo.value)


@pytest.mark.parametrize("accept", [_TEXT_ACCEPT, _REGEX_ACCEPT])
def test_text_predicate_over_an_unstructured_agent_still_loads(accept: dict[str, Any]) -> None:
    """The other side of rule 1: ``chat`` emits prose, so a substring is correct.

    Without this the guard could refuse every text predicate and still look green
    on the refusal tests above.
    """
    graph = load_workflow(
        _loop_graph(DEFAULT_AGENT_NAME, accept),
        structured_agents=STRUCTURED_AGENT_FIELDS,
    )
    assert graph.root.kind == "loop"


# ── rule 2: a json_field path must address a real field ───────────────────────


def test_json_field_path_absent_from_the_agent_schema_is_refused() -> None:
    """A misspelled path can never accept, so the loop can only time out.

    Probed before the guard existed: ``field: "pased"`` loaded clean and returned
    ``False`` for every response, making a typo indistinguishable from a model
    that does not converge.
    """
    with pytest.raises(ConfigError, match="not in the agent's schema"):
        load_workflow(
            _loop_graph(REVIEWER_AGENT_NAME, _TYPO_JSON_ACCEPT),
            structured_agents=STRUCTURED_AGENT_FIELDS,
        )


def test_the_refusal_lists_the_available_fields() -> None:
    """A path error is only actionable if it says what the legal paths are."""
    with pytest.raises(ConfigError) as excinfo:
        load_workflow(
            _loop_graph(REVIEWER_AGENT_NAME, _TYPO_JSON_ACCEPT),
            structured_agents=STRUCTURED_AGENT_FIELDS,
        )
    assert REVIEW_PASSED_FIELD in str(excinfo.value)


def test_valid_json_field_over_a_structured_agent_loads() -> None:
    """The other side of rule 2 — the recommended spelling must stay legal."""
    graph = load_workflow(
        _loop_graph(REVIEWER_AGENT_NAME, _VALID_JSON_ACCEPT),
        structured_agents=STRUCTURED_AGENT_FIELDS,
    )
    assert graph.root.kind == "loop"


def test_every_declared_schema_field_is_addressable() -> None:
    """Derivation check: each field in the map must pass, for every structured agent.

    If the map were built from the wrong part of the JSON schema (``$defs``
    instead of ``properties``, say) the specific cases above could still pass
    while most real predicates were refused.
    """
    for agent, summary in STRUCTURED_AGENT_FIELDS.items():
        assert summary.fields, f"{agent} has no addressable fields"
        for field in sorted(summary.fields):
            load_workflow(
                _loop_graph(agent, {"kind": "json_field", "field": field, "equals": True}),
                structured_agents=STRUCTURED_AGENT_FIELDS,
            )


def test_a_dotted_path_through_a_non_object_field_is_refused() -> None:
    """``steps.0.action`` reads plausible and can never resolve, so it is refused.

    ``ExecutionPlan.steps`` is an **array** of ``PlanStep``, and
    ``predicate._resolve`` walks ``Mapping`` values only — so this path returns
    ``False`` for every response and the loop could only ever end in
    ``MaxStepsExceeded``. That is the same never-accepts class the first-segment
    check exists to catch, one level down.

    An earlier revision of this module asserted the opposite: that the path
    *loads*, on the reasoning that only the first segment is validated. Doing so
    blessed exactly the defect this guard exists to prevent, which is why the map
    now carries which fields are objects.
    """
    with pytest.raises(ConfigError, match="not an object"):
        load_workflow(
            _loop_graph(
                PLANNER_AGENT_NAME,
                {"kind": "json_field", "field": "steps.0.action", "at_least": 1},
            ),
            structured_agents=STRUCTURED_AGENT_FIELDS,
        )


def test_the_dotted_path_refusal_names_the_offending_segment() -> None:
    """The error must say which segment is not an object, not merely that one is."""
    with pytest.raises(ConfigError) as excinfo:
        load_workflow(
            _loop_graph(
                PLANNER_AGENT_NAME,
                {"kind": "json_field", "field": "goal.length", "equals": 1},
            ),
            structured_agents=STRUCTURED_AGENT_FIELDS,
        )
    assert "'goal'" in str(excinfo.value)


def test_a_dotted_path_through_an_object_field_still_loads() -> None:
    """Two-sided: the rule must permit a path that *can* resolve.

    Neither shipped model has an object-valued field, so this uses a synthetic
    schema summary. Without it the rule could refuse every dotted path
    unconditionally and the refusal tests above would still pass.
    """
    graph = WorkflowGraph.model_validate(
        json.loads(
            _loop_graph(
                REVIEWER_AGENT_NAME,
                {"kind": "json_field", "field": "review.passed", "equals": True},
            )
        )
    )
    nested = {
        REVIEWER_AGENT_NAME: StructuredAgentSchema(
            fields=frozenset({"review"}), object_fields=frozenset({"review"})
        )
    }
    assert structured_acceptance_problems(graph, nested) == []


def test_a_single_segment_path_needs_no_object_field() -> None:
    """A one-segment path addresses the value directly, so objectness is irrelevant.

    ``steps`` alone is legal even though it is an array — the predicate tests that
    value, it does not walk into it.
    """
    graph = load_workflow(
        _loop_graph(
            PLANNER_AGENT_NAME,
            {"kind": "json_field", "field": "steps", "equals": ""},
        ),
        structured_agents=STRUCTURED_AGENT_FIELDS,
    )
    assert graph.root.kind == "loop"


# ── traversal: the rules must reach nested loops ──────────────────────────────


def _nested_graph(accept: dict[str, Any]) -> str:
    """A loop buried under sequence → fan_out → fan_out, plus a branch default."""
    return json.dumps(
        {
            "schema_version": WORKFLOW_SCHEMA_VERSION_CURRENT,
            "name": "nested",
            "root": {
                "kind": "sequence",
                "steps": [
                    {"kind": "agent", "agent": PLANNER_AGENT_NAME},
                    {
                        "kind": "fan_out",
                        "branches": [
                            {
                                "kind": "fan_out",
                                "branches": [
                                    {
                                        "kind": "loop",
                                        "agent": REVIEWER_AGENT_NAME,
                                        "accept": accept,
                                    }
                                ],
                            }
                        ],
                    },
                ],
            },
        }
    )


def test_a_loop_nested_inside_composite_fan_outs_is_still_checked() -> None:
    """A two-level walk would miss this; ``iter_nodes`` recurses (ADR-0018)."""
    with pytest.raises(ConfigError, match="self-report"):
        load_workflow(_nested_graph(_TEXT_ACCEPT), structured_agents=STRUCTURED_AGENT_FIELDS)


def test_a_valid_nested_loop_still_loads() -> None:
    """Two-sided: recursion must not refuse a nested loop that is correct."""
    graph = load_workflow(
        _nested_graph(_VALID_JSON_ACCEPT), structured_agents=STRUCTURED_AGENT_FIELDS
    )
    assert graph.name == "nested"


def test_a_loop_reached_through_a_branch_default_is_checked() -> None:
    """``BranchNode.default`` is a child too — omitting it would leave a blind spot."""
    source = json.dumps(
        {
            "schema_version": WORKFLOW_SCHEMA_VERSION_CURRENT,
            "name": "branch-default",
            "root": {
                "kind": "branch",
                "branches": [
                    {
                        "when": {"kind": "contains", "value": "x"},
                        "then": {"kind": "agent", "agent": DEFAULT_AGENT_NAME},
                    }
                ],
                "default": {
                    "kind": "loop",
                    "agent": REVIEWER_AGENT_NAME,
                    "accept": _TEXT_ACCEPT,
                },
            },
        }
    )
    with pytest.raises(ConfigError, match="self-report"):
        load_workflow(source, structured_agents=STRUCTURED_AGENT_FIELDS)


def test_every_problem_is_reported_not_just_the_first() -> None:
    """Two bad loops must not need two round trips to fix."""
    source = json.dumps(
        {
            "schema_version": WORKFLOW_SCHEMA_VERSION_CURRENT,
            "name": "two-problems",
            "root": {
                "kind": "sequence",
                "steps": [
                    {"kind": "loop", "agent": REVIEWER_AGENT_NAME, "accept": _TEXT_ACCEPT},
                    {"kind": "loop", "agent": PLANNER_AGENT_NAME, "accept": _TYPO_JSON_ACCEPT},
                ],
            },
        }
    )
    graph = WorkflowGraph.model_validate(json.loads(source))
    problems = structured_acceptance_problems(graph, STRUCTURED_AGENT_FIELDS)
    assert len(problems) == 2, problems
    assert any(REVIEWER_AGENT_NAME in problem for problem in problems)
    assert any(PLANNER_AGENT_NAME in problem for problem in problems)


# ── branch predicates stay out of scope, deliberately ─────────────────────────


def test_a_text_predicate_on_branch_when_is_not_refused() -> None:
    """``branch.when`` grades the node's *input*, not any agent's output.

    ``BranchNodeExecutor`` evaluates it against the last threaded message, which
    may be the original request or any upstream step's reply — there is no agent
    to bind it to, so refusing a text predicate here would be a false positive.
    Pinned as a decision so a later reader does not "complete" the guard.
    """
    source = json.dumps(
        {
            "schema_version": WORKFLOW_SCHEMA_VERSION_CURRENT,
            "name": "branch-routes-on-input",
            "root": {
                "kind": "branch",
                "branches": [
                    {
                        "when": {"kind": "contains", "value": "urgent"},
                        "then": {"kind": "agent", "agent": REVIEWER_AGENT_NAME},
                    }
                ],
                "default": {"kind": "agent", "agent": DEFAULT_AGENT_NAME},
            },
        }
    )
    graph = load_workflow(source, structured_agents=STRUCTURED_AGENT_FIELDS)
    assert graph.root.kind == "branch"


# ── iter_nodes, independently ─────────────────────────────────────────────────


def test_iter_nodes_yields_parents_before_children_and_visits_every_node() -> None:
    """The walker is the guard's reach; an under-counting walk is a silent hole."""
    graph = WorkflowGraph.model_validate(json.loads(_nested_graph(_VALID_JSON_ACCEPT)))
    kinds = [node.kind for node in iter_nodes(graph.root)]
    assert kinds == ["sequence", "agent", "fan_out", "fan_out", "loop"]


def test_iter_nodes_on_a_leaf_yields_only_that_leaf() -> None:
    graph = WorkflowGraph.model_validate(json.loads(_loop_graph(DEFAULT_AGENT_NAME, _TEXT_ACCEPT)))
    assert [node.kind for node in iter_nodes(graph.root)] == ["loop"]


def test_validate_is_a_no_op_for_a_graph_with_no_loops() -> None:
    """An all-``agent`` graph has nothing to check and must not raise."""
    graph = WorkflowGraph.model_validate(
        {
            "schema_version": WORKFLOW_SCHEMA_VERSION_CURRENT,
            "name": "no-loops",
            "root": {
                "kind": "sequence",
                "steps": [
                    {"kind": "agent", "agent": PLANNER_AGENT_NAME},
                    {"kind": "agent", "agent": REVIEWER_AGENT_NAME},
                ],
            },
        }
    )
    validate_structured_acceptance(graph, STRUCTURED_AGENT_FIELDS)


def test_iter_nodes_handles_a_branch_with_no_default() -> None:
    """``default`` is optional; the walk must not assume it is present.

    Covers the other side of the ``node.default is not None`` branch — a graph
    whose branch omits it (legal: an unmatched branch raises ``ConfigError`` at
    run time, per ADR-0016).
    """
    graph = WorkflowGraph.model_validate(
        {
            "schema_version": WORKFLOW_SCHEMA_VERSION_CURRENT,
            "name": "no-default",
            "root": {
                "kind": "branch",
                "branches": [
                    {
                        "when": {"kind": "contains", "value": "x"},
                        "then": {
                            "kind": "loop",
                            "agent": REVIEWER_AGENT_NAME,
                            "accept": _VALID_JSON_ACCEPT,
                        },
                    }
                ],
            },
        }
    )
    assert [node.kind for node in iter_nodes(graph.root)] == ["branch", "loop"]
    # And the guard still reaches the loop inside it.
    assert structured_acceptance_problems(graph, STRUCTURED_AGENT_FIELDS) == []


def test_a_json_field_spec_with_no_field_is_reported_not_crashed() -> None:
    """The defensive guard for a spec built with validation bypassed.

    ``PredicateSpec``'s model validator makes ``field``-less ``json_field``
    unreachable through ``load_workflow``. ``model_construct`` skips validation,
    which is exactly the situation ``compile_predicate`` documents its own
    compile-time guards for — so the walk must return cleanly rather than raise an
    ``AttributeError`` from deep inside a graph.
    """
    graph = WorkflowGraph.model_validate(
        {
            "schema_version": WORKFLOW_SCHEMA_VERSION_CURRENT,
            "name": "bypassed",
            "root": {
                "kind": "loop",
                "agent": REVIEWER_AGENT_NAME,
                "accept": _VALID_JSON_ACCEPT,
            },
        }
    )
    fieldless = PredicateSpec.model_construct(kind="json_field", field=None, equals=True)
    mutated = graph.model_copy(update={"root": graph.root.model_copy(update={"accept": fieldless})})
    assert structured_acceptance_problems(mutated, STRUCTURED_AGENT_FIELDS) == []
