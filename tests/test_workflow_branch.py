"""Tests for the conditional ``branch`` node (spec 0012 / ADR-0016)."""

from __future__ import annotations

import pytest

from mangomas.agents import ChatAgent
from mangomas.core import AgentContext, AgentRequest, Message, Orchestrator
from mangomas.errors import ConfigError
from mangomas.workflow import execute_workflow
from mangomas.workflow.graph import WorkflowGraph
from tests.fakes import FakeLLM


class _NamedChat(ChatAgent):
    """A ChatAgent under an arbitrary name so the response records which ran."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name


def _orch(llm: FakeLLM, *names: str) -> Orchestrator:
    orch = Orchestrator(AgentContext(llm=llm, repo=None))
    for name in names:
        orch.register(_NamedChat(name))
    return orch


def _req(content: str = "go") -> AgentRequest:
    return AgentRequest(messages=[Message(role="user", content=content)])


def _graph(root: object) -> WorkflowGraph:
    return WorkflowGraph.model_validate({"name": "t", "root": root})


def _case(value: str, agent: str) -> dict[str, object]:
    return {"when": {"kind": "contains", "value": value}, "then": {"kind": "agent", "agent": agent}}


async def test_branch_first_match_wins() -> None:
    # Both cases match "urgent"; the first declared case wins.
    graph = _graph(
        {"kind": "branch", "branches": [_case("urgent", "fast"), _case("urgent", "other")]}
    )
    result = await execute_workflow(
        graph, _req("this is urgent"), orch=_orch(FakeLLM(), "fast", "other")
    )
    assert result.agent == "fast"


async def test_branch_selects_matching_case() -> None:
    graph = _graph(
        {"kind": "branch", "branches": [_case("urgent", "fast"), _case("batch", "slow")]}
    )
    result = await execute_workflow(
        graph, _req("run the batch job"), orch=_orch(FakeLLM(), "fast", "slow")
    )
    assert result.agent == "slow"


async def test_branch_default_when_no_match() -> None:
    graph = _graph(
        {
            "kind": "branch",
            "branches": [_case("urgent", "fast")],
            "default": {"kind": "agent", "agent": "slow"},
        }
    )
    result = await execute_workflow(
        graph, _req("hello world"), orch=_orch(FakeLLM(), "fast", "slow")
    )
    assert result.agent == "slow"


async def test_branch_no_match_no_default_raises() -> None:
    graph = _graph({"kind": "branch", "branches": [_case("urgent", "fast")]})
    with pytest.raises(ConfigError):
        await execute_workflow(graph, _req("nothing here"), orch=_orch(FakeLLM(), "fast"))


async def test_branch_nested_in_sequence_routes_on_prior_output() -> None:
    # The planner's output ("a PLAN here") is threaded into the branch, which
    # routes on it → runs "exec".
    graph = _graph(
        {
            "kind": "sequence",
            "steps": [
                {"kind": "agent", "agent": "planner"},
                {
                    "kind": "branch",
                    "branches": [_case("PLAN", "exec")],
                    "default": {"kind": "agent", "agent": "fallback"},
                },
            ],
        }
    )
    llm = FakeLLM(replies=["a PLAN here", "executed"])
    result = await execute_workflow(graph, _req(), orch=_orch(llm, "planner", "exec", "fallback"))
    assert result.agent == "exec"
    assert result.content == "executed"


async def test_branch_then_is_a_fan_out() -> None:
    # A branch child may itself be a composite node (here a fan_out).
    graph = _graph(
        {
            "kind": "branch",
            "branches": [
                {
                    "when": {"kind": "contains", "value": "go"},
                    "then": {
                        "kind": "fan_out",
                        "join": "concat",
                        "branches": [
                            {"kind": "agent", "agent": "a"},
                            {"kind": "agent", "agent": "b"},
                        ],
                    },
                }
            ],
        }
    )
    result = await execute_workflow(graph, _req("go now"), orch=_orch(FakeLLM(reply="X"), "a", "b"))
    assert result.content == "X\nX"
