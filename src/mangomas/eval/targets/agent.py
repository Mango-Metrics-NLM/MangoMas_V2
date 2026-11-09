"""Agent target — dispatch a registered agent (the default eval target).

Preserves today's behaviour exactly: :meth:`run` calls
``orch.dispatch(agent, request)`` and returns ``response.content`` as the graded
text, with ``response.metadata`` carried alongside (spec-0045). ``name`` is
the agent name, so an :class:`~mangomas.eval.runner.EvalReport`'s ``agent_name``
field stays byte-identical to pre-target-indirection runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mangomas.config.llm import LLM_PROFILE_METADATA_KEY
from mangomas.errors import ConfigError
from mangomas.eval._options import require_str
from mangomas.eval.target import Target, TargetOutcome, with_usage
from mangomas.eval.target_registry import target_registry
from mangomas.utils.llm_usage import usage_scope

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import AgentRequest, Orchestrator


class AgentTarget:
    """Dispatch a single registered agent by name.

    ``llm_profile`` (spec-0045 B6; NEXT_STEPS A5) selects a named provider
    profile for every row by writing ``request.metadata["llm_profile"]`` — the
    same per-run selector spec-0040 gives API callers — so one dataset can be
    run against a local and a cloud model and the two baselines compared.
    """

    def __init__(self, *, agent: str, llm_profile: str | None = None) -> None:
        self._agent = agent
        self._llm_profile = llm_profile
        self.name = agent

    async def run(self, request: AgentRequest, *, orch: Orchestrator) -> TargetOutcome:
        if self._llm_profile is not None:
            request = request.model_copy(
                update={
                    "metadata": {**request.metadata, LLM_PROFILE_METADATA_KEY: self._llm_profile}
                }
            )
        with usage_scope() as totals:
            response = await orch.dispatch(self._agent, request)
        return TargetOutcome(
            content=response.content, metadata=with_usage(response.metadata, totals)
        )


def _agent_target_factory(options: dict[str, Any]) -> Target:
    agent = require_str(options, "agent", owner="agent target")
    profile = options.get("llm_profile")
    if profile is not None and (not isinstance(profile, str) or not profile.strip()):
        raise ConfigError(
            f"agent target: 'llm_profile' must be a non-empty string; got {profile!r}"
        )
    return AgentTarget(agent=agent, llm_profile=profile)


target_registry.register("agent", _agent_target_factory)
