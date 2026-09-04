"""Tier-1 flows I4 + I5: declarative graphs over ``POST /workflows/run``.

Two deliveries meet here, and neither has an end-to-end home:

* **The advertised product.** The shipped ``plan-execute-review`` graph is the
  planner → tool → reviewer pipeline the README promises. ``tests/
  test_plan_execute_review.py`` drives it through ``execute_workflow`` with a
  hand-built orchestrator; nothing drives it through the HTTP boundary with
  structured-output validation switched on **by environment variable**, which
  is how an operator would actually enable it.
* **The graph node kinds.** ``tests/test_workflow_api.py`` covers ``agent`` /
  ``fan_out`` / ``loop`` over HTTP but not ``branch`` (spec-0012) or a
  composite ``fan_out`` branch (spec-0013), and it hand-builds its
  orchestrator too.

Two details this file depends on, both verified against the source rather than
assumed:

* ``WorkflowRunRequest.definition`` is a **string** — inline JSON or a path
  (``api/models.py``). Posting a dict is a 422, not a graph.
* A per-request ``definition`` runs even when ``workflow.enabled`` is false
  (``api/routes/workflows.py``), so these flows deliberately do **not** set
  ``MANGOMAS_WORKFLOW__ENABLED`` — doing so would hide that documented
  behaviour rather than test it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mangomas.agents.planner import ExecutionPlan, PlanStep
from mangomas.agents.reviewer import ReviewResult
from mangomas.errors import LLMBadResponse
from tests.constants import (
    PLAN_EXECUTE_REVIEW_AGENTS,
    PLAN_EXECUTE_REVIEW_GRAPH_RELPATH,
    PLANNER_VALIDATE_OUTPUT_ENV,
    REVIEWER_VALIDATE_OUTPUT_ENV,
    STUB_REPLY,
)
from tests.fakes import FakeLLM
from tests.integration.conftest import ComposeFn

pytestmark = pytest.mark.integration

_RUN_ROUTE = "/workflows/run"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_GRAPH_PATH = _REPO_ROOT / PLAN_EXECUTE_REVIEW_GRAPH_RELPATH

_PLAN_JSON = ExecutionPlan(
    goal="ship the release",
    steps=[PlanStep(step=1, description="Build the artefact", agent="tool")],
).model_dump_json()
_TOOL_REPLY = "Build completed; artefact published."
_REVIEW_JSON = ReviewResult(passed=True, score=0.9, feedback="LGTM").model_dump_json()

_VALIDATION_ON = {
    PLANNER_VALIDATE_OUTPUT_ENV: "true",
    REVIEWER_VALIDATE_OUTPUT_ENV: "true",
}


def _body(definition: str, content: str = "ship the release") -> dict[str, Any]:
    """A ``/workflows/run`` body: the graph as a JSON *string*, plus the request."""
    return {
        "request": {"messages": [{"role": "user", "content": content}]},
        "definition": definition,
    }


def _shipped_graph_source() -> str:
    """The shipped example graph, read from disk rather than copied.

    Reading the real file is the point: a test carrying its own copy would stay
    green while the shipped artefact rotted, and that artefact is what the
    README points users at.
    """
    return _GRAPH_PATH.read_text(encoding="utf-8")


# ── I4: the shipped planner → tool → reviewer graph ───────────────────────────


async def test_shipped_graph_runs_over_http_with_validation_on(
    compose_app: ComposeFn,
) -> None:
    """The advertised pipeline, enabled the way an operator would enable it."""
    composed = compose_app(
        _VALIDATION_ON,
        llm=FakeLLM(replies=[_PLAN_JSON, _TOOL_REPLY, _REVIEW_JSON]),
    )

    async with composed.client() as client:
        response = await client.post(_RUN_ROUTE, json=_body(_shipped_graph_source()))

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["agent"] == PLAN_EXECUTE_REVIEW_AGENTS[-1]
    # Structural oracle: the final content parses as the reviewer's schema.
    assert ReviewResult.model_validate_json(payload["content"]) == ReviewResult(
        passed=True, score=0.9, feedback="LGTM"
    )
    # Every leg ran, in order — three agents, three LLM calls.
    assert len(composed.llm.calls) == len(PLAN_EXECUTE_REVIEW_AGENTS)


async def test_invalid_planner_output_returns_the_typed_envelope(
    compose_app: ComposeFn,
) -> None:
    """Validation on + a planner that emits prose → ``llm_bad_response``.

    This is the failure operators asked for when they turned the flag on: a
    typed, mapped HTTP error rather than a malformed plan threaded silently
    into the next agent.
    """
    composed = compose_app(
        _VALIDATION_ON,
        llm=FakeLLM(replies=["not a plan at all", _TOOL_REPLY, _REVIEW_JSON]),
    )

    async with composed.client() as client:
        response = await client.post(_RUN_ROUTE, json=_body(_shipped_graph_source()))

    assert response.status_code == 502, response.text
    assert response.json()["error"] == LLMBadResponse("x").code
    # The pipeline stopped at the planner — the tool leg never ran.
    assert len(composed.llm.calls) == 1


async def test_the_same_bad_output_passes_through_with_validation_off(
    compose_app: ComposeFn,
) -> None:
    """Back-compat direction: default-off restores the pre-flag contract.

    Mutation-sensitive by construction — this and the test above differ *only*
    in the two env vars, so a bug that rejected malformed output regardless of
    the flag fails here, and one that never rejected fails there. Neither test
    alone distinguishes "the flag works" from "the fake was rejected anyway".
    """
    composed = compose_app(llm=FakeLLM(replies=["not a plan at all", _TOOL_REPLY, _REVIEW_JSON]))

    async with composed.client() as client:
        response = await client.post(_RUN_ROUTE, json=_body(_shipped_graph_source()))

    assert response.status_code == 200, response.text
    assert response.json()["content"] == _REVIEW_JSON
    assert len(composed.llm.calls) == len(PLAN_EXECUTE_REVIEW_AGENTS)


# ── I5: branch and composite fan_out over HTTP ────────────────────────────────

_BRANCH_MATCH = "urgent"
_BRANCH_GRAPH = json.dumps(
    {
        "schema_version": 1,
        "name": "branch-routing",
        "root": {
            "kind": "branch",
            "branches": [
                {
                    "when": {"kind": "contains", "value": _BRANCH_MATCH},
                    "then": {"kind": "agent", "agent": "summarize"},
                }
            ],
            "default": {"kind": "agent", "agent": "chat"},
        },
    }
)

_COMPOSITE_FAN_OUT_GRAPH = json.dumps(
    {
        "schema_version": 1,
        "name": "composite-fan-out",
        "root": {
            "kind": "fan_out",
            "join": "concat",
            "branches": [
                {"kind": "agent", "agent": "chat"},
                {
                    "kind": "loop",
                    "agent": "summarize",
                    "max_steps": 1,
                    "accept": {"kind": "contains", "value": STUB_REPLY},
                },
            ],
        },
    }
)


async def test_branch_predicate_routes_to_the_matching_agent(
    compose_app: ComposeFn,
) -> None:
    """A matching predicate selects its case; a non-match falls to ``default``.

    Both directions in one test because the pair is the contract: a branch that
    always took the first case, and one that always took the default, each pass
    half of this and fail the other.
    """
    composed = compose_app(llm=FakeLLM(reply=STUB_REPLY))

    async with composed.client() as client:
        matched = await client.post(
            _RUN_ROUTE, json=_body(_BRANCH_GRAPH, f"this is {_BRANCH_MATCH}, act now")
        )
        unmatched = await client.post(_RUN_ROUTE, json=_body(_BRANCH_GRAPH, "take your time"))

    assert matched.status_code == 200, matched.text
    assert matched.json()["agent"] == "summarize"
    assert unmatched.status_code == 200, unmatched.text
    assert unmatched.json()["agent"] == "chat"


async def test_composite_fan_out_joins_every_branch(compose_app: ComposeFn) -> None:
    """A ``fan_out`` whose branch is itself a composite node (spec-0013).

    The all-agent fast path delegates to ``dispatch_fan_out``; a composite
    branch goes through ``resolve_executor`` under ``asyncio.gather`` instead.
    This graph mixes both kinds, so it exercises the path the fast path skips.
    """
    composed = compose_app(llm=FakeLLM(reply=STUB_REPLY))

    async with composed.client() as client:
        response = await client.post(_RUN_ROUTE, json=_body(_COMPOSITE_FAN_OUT_GRAPH))

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["agent"] == "fan_out"
    # `concat` newline-joins each branch's content — one line per branch.
    assert payload["content"].split("\n") == [STUB_REPLY, STUB_REPLY]
