"""Tests for :class:`mangomas.workflow.runner.WorkflowRunner` (spec 0005 / ADR-0007).

The runner compiles a graph onto the orchestrator primitives. The load-bearing
guarantee is that a *linear* graph reproduces ``dispatch_pipeline`` exactly; the
fan-out and acceptance-loop paths reuse ``dispatch_fan_out`` and the acceptance
loop respectively.
"""

from __future__ import annotations

import pytest

from mangomas.core import AgentContext, AgentRequest, AgentResponse, Message, Orchestrator
from mangomas.errors import AgentNotFound, MaxStepsExceeded
from mangomas.workflow import WorkflowGraph, WorkflowNode, WorkflowRunner
from tests.fakes import FakeLLM


class _SuffixAgent:
    """Append a fixed suffix to the last user message (stateless, deterministic)."""

    def __init__(self, name: str, suffix: str) -> None:
        self.name = name
        self._suffix = suffix

    async def handle(self, request: AgentRequest, _ctx: AgentContext) -> AgentResponse:
        content = f"{request.messages[-1].content}{self._suffix}"
        return AgentResponse(content=content, agent=self.name)


class _ScriptedAgent:
    """Return successive canned replies and record every request it handled."""

    def __init__(self, name: str, replies: list[str]) -> None:
        self.name = name
        self._replies = replies
        self.seen: list[AgentRequest] = []

    async def handle(self, request: AgentRequest, _ctx: AgentContext) -> AgentResponse:
        self.seen.append(request)
        index = min(len(self.seen) - 1, len(self._replies) - 1)
        return AgentResponse(content=self._replies[index], agent=self.name)


def _orch(*agents: object) -> Orchestrator:
    orch = Orchestrator(AgentContext(llm=FakeLLM(), repo=None))
    for agent in agents:
        orch.register(agent)  # type: ignore[arg-type]
    return orch


def _req(content: str = "x") -> AgentRequest:
    return AgentRequest(messages=[Message(role="user", content=content)])


# ── Single node ───────────────────────────────────────────────────────────────


async def test_single_node_returns_agent_response() -> None:
    orch = _orch(_ScriptedAgent("a", ["only"]))
    graph = WorkflowGraph(nodes=(WorkflowNode(id="n", agent="a"),))
    resp = await WorkflowRunner(graph).run(_req(), orch=orch)
    assert resp.content == "only"
    assert resp.agent == "a"
    # dispatch stamps loop telemetry — threaded through unchanged.
    assert resp.metadata["loop"]["steps_taken"] == 1


def test_runner_exposes_graph() -> None:
    graph = WorkflowGraph(nodes=(WorkflowNode(id="n", agent="a"),))
    assert WorkflowRunner(graph).graph is graph


# ── Linear graph == dispatch_pipeline (the load-bearing equivalence) ───────────


async def test_linear_graph_matches_dispatch_pipeline() -> None:
    orch = _orch(_SuffixAgent("a", "A"), _SuffixAgent("b", "B"), _SuffixAgent("c", "C"))
    graph = WorkflowGraph(
        nodes=(
            WorkflowNode(id="a", agent="a"),
            WorkflowNode(id="b", agent="b", depends_on=("a",)),
            WorkflowNode(id="c", agent="c", depends_on=("b",)),
        )
    )
    workflow_resp = await WorkflowRunner(graph).run(_req("x"), orch=orch)
    pipeline_resp = await orch.dispatch_pipeline(["a", "b", "c"], _req("x"))
    assert workflow_resp.content == pipeline_resp.content == "xABC"


# ── Fan-out levels ────────────────────────────────────────────────────────────


async def test_fan_out_concat_join() -> None:
    orch = _orch(_ScriptedAgent("a", ["alpha"]), _ScriptedAgent("b", ["beta"]))
    graph = WorkflowGraph(
        join="concat",
        nodes=(WorkflowNode(id="a", agent="a"), WorkflowNode(id="b", agent="b")),
    )
    resp = await WorkflowRunner(graph).run(_req(), orch=orch)
    assert resp.content == "alpha\nbeta"
    assert resp.agent.startswith("workflow.fan_out")
    assert resp.metadata["fan_out"] == {
        "join": "concat",
        "nodes": ["a", "b"],
        "agents": ["a", "b"],
    }


async def test_fan_out_with_acceptance_loop_uses_per_node_fallback() -> None:
    """A fan-out level containing an acceptance-loop node falls back off dispatch_fan_out."""
    looper = _ScriptedAgent("loop", ["no", "DONE"])
    plain = _ScriptedAgent("plain", ["p"])
    orch = _orch(looper, plain)
    graph = WorkflowGraph(
        join="concat",
        nodes=(
            WorkflowNode(id="a", agent="loop", until="DONE", max_steps=3),
            WorkflowNode(id="b", agent="plain"),
        ),
    )
    resp = await WorkflowRunner(graph).run(_req(), orch=orch)
    assert resp.content == "DONE\np"
    assert len(looper.seen) == 2  # looped until the marker appeared


async def test_fan_out_first_join() -> None:
    orch = _orch(_ScriptedAgent("a", ["alpha"]), _ScriptedAgent("b", ["beta"]))
    graph = WorkflowGraph(
        join="first",
        nodes=(WorkflowNode(id="a", agent="a"), WorkflowNode(id="b", agent="b")),
    )
    resp = await WorkflowRunner(graph).run(_req(), orch=orch)
    assert resp.content == "alpha"


async def test_diamond_joins_then_feeds_downstream() -> None:
    orch = _orch(
        _ScriptedAgent("s", ["seed"]),
        _ScriptedAgent("a", ["alpha"]),
        _ScriptedAgent("b", ["beta"]),
        _SuffixAgent("j", "!"),
    )
    graph = WorkflowGraph(
        join="concat",
        nodes=(
            WorkflowNode(id="s", agent="s"),
            WorkflowNode(id="a", agent="a", depends_on=("s",)),
            WorkflowNode(id="b", agent="b", depends_on=("s",)),
            WorkflowNode(id="j", agent="j", depends_on=("a", "b")),
        ),
    )
    resp = await WorkflowRunner(graph).run(_req(), orch=orch)
    # The join node receives the concatenated fan-out output as its input.
    assert resp.content == "alpha\nbeta!"


# ── Acceptance loop (until / max_steps) ───────────────────────────────────────


async def test_acceptance_loop_stops_on_marker() -> None:
    agent = _ScriptedAgent("a", ["no", "no", "DONE"])
    orch = _orch(agent)
    graph = WorkflowGraph(nodes=(WorkflowNode(id="n", agent="a", until="DONE", max_steps=5),))
    resp = await WorkflowRunner(graph).run(_req(), orch=orch)
    assert resp.content == "DONE"
    assert len(agent.seen) == 3
    assert resp.metadata["loop"]["accepted"] is True


async def test_acceptance_loop_exhausted_raises() -> None:
    orch = _orch(_ScriptedAgent("a", ["no"]))
    graph = WorkflowGraph(nodes=(WorkflowNode(id="n", agent="a", until="DONE", max_steps=2),))
    with pytest.raises(MaxStepsExceeded):
        await WorkflowRunner(graph).run(_req(), orch=orch)


# ── Fail-fast on unknown agent ────────────────────────────────────────────────


async def test_unknown_agent_fails_fast_without_dispatch() -> None:
    known = _ScriptedAgent("known", ["ok"])
    orch = _orch(known)
    graph = WorkflowGraph(
        nodes=(
            WorkflowNode(id="n1", agent="known"),
            WorkflowNode(id="n2", agent="ghost", depends_on=("n1",)),
        )
    )
    with pytest.raises(AgentNotFound):
        await WorkflowRunner(graph).run(_req(), orch=orch)
    # Validation happens before any dispatch, so the valid agent never ran.
    assert known.seen == []
