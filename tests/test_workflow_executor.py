"""Executor tests — parity with the imperative dispatch_* methods + composition."""

from __future__ import annotations

import pytest

from mangomas.agents import ChatAgent
from mangomas.core import AgentContext, AgentRequest, Message, Orchestrator
from mangomas.errors import AgentNotFound, MaxStepsExceeded
from mangomas.workflow import NodeExecutor, execute_workflow
from mangomas.workflow.graph import AgentNode, SequenceNode, WorkflowGraph
from mangomas.workflow.registry import resolve_executor
from tests.constants import WORKFLOW_LOOP_SENTINEL
from tests.fakes import FakeLLM


class _NamedChat(ChatAgent):
    """A ChatAgent registered under an arbitrary name (distinct dispatch target)."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name


def _orch(llm: FakeLLM, *names: str) -> Orchestrator:
    ctx = AgentContext(llm=llm, repo=None)
    orch = Orchestrator(ctx)
    for name in names:
        orch.register(_NamedChat(name))
    return orch


def _req(content: str = "go") -> AgentRequest:
    return AgentRequest(messages=[Message(role="user", content=content)])


def _graph(root: object) -> WorkflowGraph:
    return WorkflowGraph.model_validate({"name": "t", "root": root})


# ── sequence parity with dispatch_pipeline ────────────────────────────────────


async def test_sequence_of_agents_equals_dispatch_pipeline() -> None:
    graph = _graph(
        {
            "kind": "sequence",
            "steps": [{"kind": "agent", "agent": "a"}, {"kind": "agent", "agent": "b"}],
        }
    )
    wf_llm = FakeLLM(replies=["step-one", "step-two"])
    wf_result = await execute_workflow(graph, _req(), orch=_orch(wf_llm, "a", "b"))

    pipe_llm = FakeLLM(replies=["step-one", "step-two"])
    pipe_result = await _orch(pipe_llm, "a", "b").dispatch_pipeline(["a", "b"], _req())

    # Full-model parity holds because executors are metadata-transparent.
    assert wf_result == pipe_result
    # Threading: the second agent receives the first agent's output as its input.
    assert wf_llm.calls[1][0].content == "step-one"
    assert wf_result.content == "step-two"


async def test_planner_tool_reviewer_shape_matches_pipeline() -> None:
    """Spec 0005 acceptance criterion: the canonical three-agent sequence."""
    names = ("planner", "tool", "reviewer")
    root = {"kind": "sequence", "steps": [{"kind": "agent", "agent": n} for n in names]}
    wf = await execute_workflow(_graph(root), _req(), orch=_orch(FakeLLM(reply="R"), *names))
    pipe = await _orch(FakeLLM(reply="R"), *names).dispatch_pipeline(list(names), _req())
    assert wf == pipe


async def test_root_agent_node_equals_single_dispatch() -> None:
    graph = _graph({"kind": "agent", "agent": "solo"})
    wf = await execute_workflow(graph, _req(), orch=_orch(FakeLLM(reply="hi"), "solo"))
    direct = await _orch(FakeLLM(reply="hi"), "solo").dispatch("solo", _req())
    assert wf == direct


# ── fan_out parity with dispatch_fan_out ──────────────────────────────────────


async def test_fan_out_first_returns_first_branch_verbatim() -> None:
    graph = _graph(
        {
            "kind": "fan_out",
            "join": "first",
            "branches": [{"kind": "agent", "agent": "a"}, {"kind": "agent", "agent": "b"}],
        }
    )
    wf = await execute_workflow(graph, _req(), orch=_orch(FakeLLM(reply="one"), "a", "b"))
    fan = await _orch(FakeLLM(reply="one"), "a", "b").dispatch_fan_out(["a", "b"], _req())
    assert wf == fan[0]


async def test_fan_out_concat_joins_branch_contents() -> None:
    graph = _graph(
        {
            "kind": "fan_out",
            "join": "concat",
            "branches": [{"kind": "agent", "agent": "a"}, {"kind": "agent", "agent": "b"}],
        }
    )
    wf = await execute_workflow(graph, _req(), orch=_orch(FakeLLM(reply="X"), "a", "b"))
    assert wf.content == "X\nX"
    assert wf.agent == "fan_out"


async def test_fan_out_unknown_agent_raises() -> None:
    graph = _graph({"kind": "fan_out", "branches": [{"kind": "agent", "agent": "ghost"}]})
    with pytest.raises(AgentNotFound):
        await execute_workflow(graph, _req(), orch=_orch(FakeLLM(), "a"))


# ── loop parity with dispatch acceptance loop ─────────────────────────────────


async def test_loop_accepts_on_sentinel() -> None:
    graph = _graph(
        {
            "kind": "loop",
            "agent": "a",
            "max_steps": 3,
            "accept": {"kind": "contains", "value": WORKFLOW_LOOP_SENTINEL},
        }
    )
    llm = FakeLLM(replies=["nope", WORKFLOW_LOOP_SENTINEL])
    result = await execute_workflow(graph, _req(), orch=_orch(llm, "a"))
    assert WORKFLOW_LOOP_SENTINEL in result.content
    assert result.metadata["loop"] == {"steps_taken": 2, "accepted": True}


async def test_loop_never_accepts_raises_max_steps() -> None:
    graph = _graph(
        {
            "kind": "loop",
            "agent": "a",
            "max_steps": 2,
            "accept": {"kind": "contains", "value": WORKFLOW_LOOP_SENTINEL},
        }
    )
    llm = FakeLLM(replies=["nope", "still nope"])
    with pytest.raises(MaxStepsExceeded):
        await execute_workflow(graph, _req(), orch=_orch(llm, "a"))


# ── composition / nesting ─────────────────────────────────────────────────────


async def test_fan_out_nested_in_sequence() -> None:
    graph = _graph(
        {
            "kind": "sequence",
            "steps": [
                {"kind": "agent", "agent": "a"},
                {
                    "kind": "fan_out",
                    "join": "concat",
                    "branches": [{"kind": "agent", "agent": "b"}, {"kind": "agent", "agent": "c"}],
                },
            ],
        }
    )
    result = await execute_workflow(graph, _req(), orch=_orch(FakeLLM(reply="Z"), "a", "b", "c"))
    assert result.content == "Z\nZ"
    assert result.agent == "fan_out"


async def test_loop_nested_in_sequence() -> None:
    graph = _graph(
        {
            "kind": "sequence",
            "steps": [
                {"kind": "agent", "agent": "a"},
                {
                    "kind": "loop",
                    "agent": "b",
                    "max_steps": 2,
                    "accept": {"kind": "contains", "value": WORKFLOW_LOOP_SENTINEL},
                },
            ],
        }
    )
    llm = FakeLLM(replies=["first", WORKFLOW_LOOP_SENTINEL])
    result = await execute_workflow(graph, _req(), orch=_orch(llm, "a", "b"))
    assert WORKFLOW_LOOP_SENTINEL in result.content


async def test_sequence_unknown_agent_raises() -> None:
    graph = _graph(
        {
            "kind": "sequence",
            "steps": [{"kind": "agent", "agent": "a"}, {"kind": "agent", "agent": "ghost"}],
        }
    )
    with pytest.raises(AgentNotFound):
        await execute_workflow(graph, _req(), orch=_orch(FakeLLM(), "a"))


async def test_node_executors_satisfy_protocol() -> None:
    executor = resolve_executor(SequenceNode(steps=[AgentNode(agent="a")]))
    assert isinstance(executor, NodeExecutor)


# ── spec-0040: the run-scoped profile survives a concat join ──────────────────


async def test_concat_fan_out_followed_by_agent_keeps_the_profile() -> None:
    """A ``concat`` join synthesises its response; the selector must still thread on.

    Without the carry in ``nodes/fan_out.py`` the branches would run on the
    profile client and the step *after* the join would silently fall back to
    the default client — the mixed-provider run the user asked for, half
    honoured. Mutation proof: return ``metadata={}`` from the join again and
    the third call lands on ``default_llm``.
    """
    from mangomas.agents._prompt import LLM_PROFILES_EXTRAS_KEY  # noqa: PLC0415
    from mangomas.composition.builder import _Orchestrator  # noqa: PLC0415
    from mangomas.config import LLM_PROFILE_METADATA_KEY  # noqa: PLC0415

    default_llm = FakeLLM(reply="default")
    profile_llm = FakeLLM(reply="profile")
    ctx = AgentContext(
        llm=default_llm,
        repo=None,
        extras={LLM_PROFILES_EXTRAS_KEY: {"fast": profile_llm}},
    )
    orch = _Orchestrator(ctx)
    for name in ("a", "b", "c"):
        orch.register(_NamedChat(name))
    graph = _graph(
        {
            "kind": "sequence",
            "steps": [
                {
                    "kind": "fan_out",
                    "join": "concat",
                    "branches": [{"kind": "agent", "agent": "a"}, {"kind": "agent", "agent": "b"}],
                },
                {"kind": "agent", "agent": "c"},
            ],
        }
    )
    request = AgentRequest(
        messages=[Message(role="user", content="go")],
        metadata={LLM_PROFILE_METADATA_KEY: "fast"},
    )
    wf = await execute_workflow(graph, request, orch=orch)
    assert len(profile_llm.calls) == 3, "both branches and the step after the join"
    assert default_llm.calls == []
    assert wf.agent == "c"
    assert wf.metadata[LLM_PROFILE_METADATA_KEY] == "fast"


async def test_concat_fan_out_without_profile_stays_metadata_empty() -> None:
    """Default-OFF half: a run that names no profile gets the join's old empty metadata."""
    graph = _graph(
        {
            "kind": "fan_out",
            "join": "concat",
            "branches": [{"kind": "agent", "agent": "a"}, {"kind": "agent", "agent": "b"}],
        }
    )
    wf = await execute_workflow(graph, _req(), orch=_orch(FakeLLM(reply="X"), "a", "b"))
    assert wf.metadata == {}
