"""Composite fan_out branches (spec 0013 / ADR-0018).

The all-agent path keeps delegating to ``dispatch_fan_out`` (parity); a composite
branch (``loop`` / ``branch`` / nested ``fan_out``) runs via its executor.
"""

from __future__ import annotations

from mangomas.agents import ChatAgent
from mangomas.core import AgentContext, AgentRequest, Message, Orchestrator
from mangomas.workflow import execute_workflow
from mangomas.workflow.graph import WorkflowGraph
from tests.fakes import FakeLLM


class _NamedChat(ChatAgent):
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


async def test_fan_out_all_agent_parity() -> None:
    # All-agent fan_out still delegates to dispatch_fan_out → byte-identical join.
    graph = _graph(
        {
            "kind": "fan_out",
            "join": "concat",
            "branches": [{"kind": "agent", "agent": "a"}, {"kind": "agent", "agent": "b"}],
        }
    )
    result = await execute_workflow(graph, _req(), orch=_orch(FakeLLM(reply="X"), "a", "b"))
    assert result.content == "X\nX"
    assert result.agent == "fan_out"


async def test_fan_out_composite_loop_branch() -> None:
    # One branch is a loop (composite) → runs via its executor under gather.
    graph = _graph(
        {
            "kind": "fan_out",
            "join": "concat",
            "branches": [
                {"kind": "agent", "agent": "a"},
                {
                    "kind": "loop",
                    "agent": "b",
                    "accept": {"kind": "contains", "value": "X"},
                    "max_steps": 3,
                },
            ],
        }
    )
    result = await execute_workflow(graph, _req(), orch=_orch(FakeLLM(reply="X"), "a", "b"))
    assert result.content == "X\nX"


async def test_fan_out_composite_branch_child_first_join() -> None:
    # A fan_out branch that is itself a `branch` node; join=first returns it verbatim.
    graph = _graph(
        {
            "kind": "fan_out",
            "join": "first",
            "branches": [
                {
                    "kind": "branch",
                    "branches": [
                        {
                            "when": {"kind": "contains", "value": "go"},
                            "then": {"kind": "agent", "agent": "fast"},
                        }
                    ],
                    "default": {"kind": "agent", "agent": "slow"},
                },
                {"kind": "agent", "agent": "other"},
            ],
        }
    )
    result = await execute_workflow(
        graph, _req("go now"), orch=_orch(FakeLLM(reply="X"), "fast", "slow", "other")
    )
    assert result.agent == "fast"  # first branch (the branch node) ran "fast"
