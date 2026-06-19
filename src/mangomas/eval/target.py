"""Target protocol — what an eval run dispatches each row against.

A :class:`Target` decouples the :class:`~mangomas.eval.runner.EvalRunner` from a
single registered agent: a run can target a registered agent (the default,
preserving today's behaviour), a multi-agent pipeline / fan-out topology, or a
deterministic ``echo`` baseline (used for regression comparison and tests).
Targets are resolved by name through
:data:`~mangomas.eval.target_registry.target_registry` exactly as scorers and
sinks are.

``run`` is async (real targets perform I/O through the orchestrator) and returns
the prediction string the scorer grades. The orchestrator is supplied per call —
it is only available at runtime inside the runner — mirroring how
:meth:`Scorer.score` receives its :class:`ScorerContext`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import AgentRequest, Orchestrator


@runtime_checkable
class Target(Protocol):
    """A named, dispatchable evaluation target."""

    name: str

    async def run(self, request: AgentRequest, *, orch: Orchestrator) -> str:
        """Produce a prediction string for *request* using *orch*."""
        ...
