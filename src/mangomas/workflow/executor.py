"""Workflow driver: execute a :class:`WorkflowGraph` against a live orchestrator.

A :class:`NodeExecutor` runs one node and returns an ``AgentResponse``, so nodes
compose (content *and* metadata thread between them). The orchestrator is
supplied per call — mirroring :meth:`mangomas.eval.target.Target.run` — because
it is only available at runtime. Executors are **metadata-transparent**: they
return the underlying ``dispatch*`` result verbatim (all provenance goes to
spans), so an all-agent ``sequence`` is byte-identical to ``dispatch_pipeline``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from opentelemetry import trace

from mangomas.workflow.registry import resolve_executor

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import AgentRequest, AgentResponse, Orchestrator
    from mangomas.workflow.graph import WorkflowGraph


@runtime_checkable
class NodeExecutor(Protocol):
    """Runs one workflow node against a live orchestrator."""

    async def run(self, request: AgentRequest, *, orch: Orchestrator) -> AgentResponse:
        """Execute the node and return its response."""
        ...


async def execute_workflow(
    graph: WorkflowGraph, request: AgentRequest, *, orch: Orchestrator
) -> AgentResponse:
    """Execute *graph*'s root node against *orch* and return the final response."""
    with trace.get_tracer(__name__).start_as_current_span("workflow.execute") as span:
        span.set_attribute("workflow.name", graph.name)
        span.set_attribute("workflow.root_kind", graph.root.kind)
        return await resolve_executor(graph.root).run(request, orch=orch)
