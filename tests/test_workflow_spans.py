"""Span-scope tests for workflow node executors.

Every node executor must finish its *whole* unit of work inside its
``workflow.node.<kind>`` span — including the fan_out join and the sequence's
final return — so the span end timestamp covers the node to completion.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from mangomas.agents import ChatAgent
from mangomas.core import AgentContext, AgentRequest, Message, Orchestrator
from mangomas.workflow import execute_workflow, node_registry
from mangomas.workflow.graph import WorkflowGraph
from tests.fakes import FakeLLM

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import ReadableSpan

# ── Module-level span exporter ────────────────────────────────────────────────
# The OTel global provider can only be promoted once (ProxyTracerProvider →
# TracerProvider).  If another test module already promoted it we add our
# InMemorySpanExporter to that provider so spans still flow to _EXPORTER
# (same idiom as tests/test_tracing.py).

_EXPORTER: InMemorySpanExporter = InMemorySpanExporter()


def _init_exporter() -> None:
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
    else:
        new_provider = TracerProvider()
        new_provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
        trace.set_tracer_provider(new_provider)


_init_exporter()


# ── Helpers ───────────────────────────────────────────────────────────────────


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


def _span(name: str) -> ReadableSpan:
    matches = [s for s in _EXPORTER.get_finished_spans() if s.name == name]
    assert matches, f"no finished span named {name!r}"
    return matches[-1]


class _SpanProbeResponse:
    """Duck-typed response whose ``content`` records the active span when read."""

    def __init__(self, content: str, seen: list[trace.Span]) -> None:
        self._content = content
        self._seen = seen

    @property
    def content(self) -> str:
        self._seen.append(trace.get_current_span())
        return self._content


class _SpanProbeExecutor:
    """Stand-in composite-branch executor returning a span-probing response."""

    def __init__(self, content: str, seen: list[trace.Span]) -> None:
        self._content = content
        self._seen = seen

    async def run(
        self,
        request: AgentRequest,  # noqa: ARG002 — protocol signature
        *,
        orch: Orchestrator,  # noqa: ARG002 — protocol signature
    ) -> _SpanProbeResponse:
        return _SpanProbeResponse(self._content, self._seen)


# ── fan_out: the join happens inside the node span ────────────────────────────


async def test_fan_out_concat_join_runs_inside_node_span() -> None:
    # A composite (loop) branch forces the gather path; its executor is swapped
    # for a probe whose `.content` records the span active *during the join*.
    # The join must therefore see the workflow.node.fan_out span — if the join
    # ran after the `with` block, it would see the enclosing workflow.execute
    # span instead.
    _EXPORTER.clear()
    seen: list[trace.Span] = []
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
                    "max_steps": 1,
                },
            ],
        }
    )

    def _probe_factory(_node: object) -> _SpanProbeExecutor:
        return _SpanProbeExecutor("X", seen)

    with node_registry.scoped("loop", _probe_factory):  # type: ignore[arg-type]
        result = await execute_workflow(graph, _req(), orch=_orch(FakeLLM(reply="X"), "a", "b"))

    assert result.content == "X\nX"
    assert len(seen) == 1  # the join read the probe's content exactly once
    node_span = _span("workflow.node.fan_out")
    assert seen[0].get_span_context().span_id == node_span.context.span_id


async def test_fan_out_node_span_covers_child_dispatch() -> None:
    # All-agent path: the dispatch_fan_out child span must be parented to the
    # node span, and the node span must end at-or-after it (join inside).
    _EXPORTER.clear()
    graph = _graph(
        {
            "kind": "fan_out",
            "join": "concat",
            "branches": [{"kind": "agent", "agent": "a"}, {"kind": "agent", "agent": "b"}],
        }
    )
    await execute_workflow(graph, _req(), orch=_orch(FakeLLM(reply="X"), "a", "b"))

    node_span = _span("workflow.node.fan_out")
    child = _span("orchestrator.dispatch_fan_out")
    assert child.parent is not None
    assert child.parent.span_id == node_span.context.span_id
    assert node_span.end_time is not None
    assert child.end_time is not None
    assert node_span.end_time >= child.end_time


# ── sequence: the final return happens inside the node span ───────────────────


async def test_sequence_node_span_covers_all_steps() -> None:
    _EXPORTER.clear()
    graph = _graph(
        {
            "kind": "sequence",
            "steps": [{"kind": "agent", "agent": "a"}, {"kind": "agent", "agent": "b"}],
        }
    )
    await execute_workflow(graph, _req(), orch=_orch(FakeLLM(reply="X"), "a", "b"))

    node_span = _span("workflow.node.sequence")
    agent_spans = [s for s in _EXPORTER.get_finished_spans() if s.name == "workflow.node.agent"]
    assert len(agent_spans) == 2
    assert node_span.end_time is not None
    for step_span in agent_spans:
        assert step_span.parent is not None
        assert step_span.parent.span_id == node_span.context.span_id
        assert step_span.end_time is not None
        assert node_span.end_time >= step_span.end_time
