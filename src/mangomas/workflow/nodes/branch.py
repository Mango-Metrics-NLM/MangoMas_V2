"""``branch`` node — select exactly one child by predicate (first match wins).

Predicates are compiled once (reusing :func:`compile_predicate`) and evaluated in
declared order against the node's **input content** — the last message threaded
into the node — wrapped in a synthetic ``AgentResponse``. The first matching
case's ``then`` runs via :func:`resolve_executor`; ``default`` runs when no case
matches; with no ``default`` an unmatched branch raises ``ConfigError`` (ADR-0016).
The node adds no back-edge, so the graph stays an acyclic tree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from opentelemetry import trace

from mangomas.core import AgentResponse
from mangomas.errors import ConfigError
from mangomas.workflow.graph import BranchNode
from mangomas.workflow.predicate import compile_predicate
from mangomas.workflow.registry import node_registry, resolve_executor

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import AgentRequest, Orchestrator
    from mangomas.workflow.executor import NodeExecutor
    from mangomas.workflow.graph import WorkflowNode


def _input_content(request: AgentRequest) -> str:
    """Content the branch routes on: the last threaded message (or empty)."""
    return request.messages[-1].content if request.messages else ""


class BranchNodeExecutor:
    """Run the first case whose compiled predicate matches the input content."""

    def __init__(self, node: BranchNode) -> None:
        # Compile each predicate once; keep (predicate, then-node) pairs in order.
        self._cases = [(compile_predicate(case.when), case.then) for case in node.branches]
        self._default = node.default

    async def run(self, request: AgentRequest, *, orch: Orchestrator) -> AgentResponse:
        with trace.get_tracer(__name__).start_as_current_span("workflow.node.branch") as span:
            probe = AgentResponse(content=_input_content(request), agent="branch")
            for index, (predicate, then) in enumerate(self._cases):
                if predicate(probe):
                    # Stringify so the attribute is uniformly typed with the
                    # "default" path below (OTel wants one value type per key).
                    span.set_attribute("branch.selected", str(index))
                    return await resolve_executor(then).run(request, orch=orch)
            if self._default is not None:
                span.set_attribute("branch.selected", "default")
                return await resolve_executor(self._default).run(request, orch=orch)
            raise ConfigError("branch matched no case and has no default")


def _branch_factory(node: WorkflowNode) -> NodeExecutor:
    if not isinstance(node, BranchNode):  # pragma: no cover — guarded by the kind discriminator
        raise ConfigError(f"branch executor requires a BranchNode; got {node.kind!r}")
    return BranchNodeExecutor(node)


node_registry.register("branch", _branch_factory)
