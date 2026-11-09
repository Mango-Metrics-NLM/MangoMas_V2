"""Fan-out target — evaluate a parallel multi-agent fan-out.

All agents receive the same request via
:meth:`~mangomas.core.Orchestrator.dispatch_fan_out`; the responses are joined
into a single prediction string. ``join`` selects the strategy:

``first`` (default)
    Use the first agent's ``content`` (the canonical answer).
``concat``
    Newline-join every agent's ``content`` (useful for ensemble scoring).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mangomas.errors import ConfigError
from mangomas.eval._options import require_list
from mangomas.eval.target import Target, TargetOutcome, with_usage
from mangomas.eval.target_registry import target_registry
from mangomas.utils.llm_usage import usage_scope

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import AgentRequest, Orchestrator

logger = logging.getLogger(__name__)

_VALID_JOINS = ("first", "concat")
_DEFAULT_JOIN = "first"


class FanOutTarget:
    """Dispatch agents in parallel and join their responses."""

    name = "fan_out"

    def __init__(self, agents: list[str], *, join: str = _DEFAULT_JOIN) -> None:
        self._agents = agents
        self._join = join

    async def run(self, request: AgentRequest, *, orch: Orchestrator) -> TargetOutcome:
        # The orchestrator's own "Fan-out complete" debug log has no visibility
        # into the *join* strategy — that's this target's own configuration —
        # so it is worth a dedicated line here rather than assumed from context.
        logger.debug(
            "Fan-out target dispatching",
            extra={
                "event": "eval_fan_out_target_run",
                "agents": self._agents,
                "join": self._join,
            },
        )
        with usage_scope() as totals:
            responses = await orch.dispatch_fan_out(self._agents, request)
        # Every branch's metadata is kept, keyed by position, so a scorer can
        # see each agent's tool trace even when only one branch's content is
        # graded; usage is the scope's total across all branches (the gathered
        # tasks share the scope's accumulator).
        metadata: dict[str, Any] = with_usage(
            {"fan_out": [{"agent": r.agent, "metadata": dict(r.metadata)} for r in responses]},
            totals,
        )
        if self._join == "concat":
            return TargetOutcome("\n".join(r.content for r in responses), metadata)
        return TargetOutcome(responses[0].content, metadata)


def _fan_out_target_factory(options: dict[str, Any]) -> Target:
    agents = require_list(options, "agents", owner="fan_out target")
    if not agents:
        raise ConfigError("fan_out target requires a non-empty 'agents' list")
    join = str(options.get("join", _DEFAULT_JOIN))
    if join not in _VALID_JOINS:
        raise ConfigError(f"fan_out target 'join' must be one of {_VALID_JOINS}; got {join!r}")
    return FanOutTarget([str(a) for a in agents], join=join)


target_registry.register("fan_out", _fan_out_target_factory)
