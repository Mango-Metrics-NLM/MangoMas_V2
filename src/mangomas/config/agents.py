"""Per-agent overrides and the orchestrator control loop.

`MANGOMAS_AGENTS__<NAME>__*` and `MANGOMAS_LOOP__*`."""

from __future__ import annotations

from pydantic import BaseModel

# ToolAgent: cap on the total number of LLM calls per request. Overridable
# per agent via ``MANGOMAS_AGENTS__<NAME>__MAX_TOOL_STEPS`` (AgentSettings).
DEFAULT_TOOL_MAX_STEPS: int = 5


# SummarizeAgent: how many persisted turns to pull into the summary context.
# Overridable per agent via ``MANGOMAS_AGENTS__<NAME>__HISTORY_LIMIT``
# (AgentSettings), mirroring DEFAULT_TOOL_MAX_STEPS above.
DEFAULT_SUMMARIZE_HISTORY_LIMIT: int = 10


# Structured-output agents (planner/reviewer): validate the LLM's JSON reply
# against the agent's schema after each ``handle`` call, raising
# ``LLMBadResponse`` on mismatch. Off by default so existing deployments see
# no behaviour change. Overridable per agent via
# ``MANGOMAS_AGENTS__<NAME>__VALIDATE_OUTPUT`` (AgentSettings).
DEFAULT_VALIDATE_OUTPUT: bool = False


class AgentSettings(BaseModel):
    """Per-agent overrides loaded from ``MANGOMAS_AGENTS__<NAME>__*`` env vars."""

    system_prompt: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    # Reserved for a follow-up spec (spec-0014 M5 activates temperature and
    # max_tokens only): per-agent client/model selection needs a
    # composition-layer change (a per-agent LLMClient rather than one shared
    # `ctx.llm`) that is out of scope here. Not read by any agent yet.
    model_override: str | None = None
    # ToolAgent only: cap on the total number of LLM calls per request.
    # ``None`` (the default) falls back to ``DEFAULT_TOOL_MAX_STEPS``, so
    # existing environments see no behaviour change.
    max_tool_steps: int | None = None
    # SummarizeAgent only: how many persisted turns to load into the summary
    # context. ``None`` (the default) falls back to
    # ``DEFAULT_SUMMARIZE_HISTORY_LIMIT``, so existing environments see no
    # behaviour change.
    history_limit: int | None = None
    # Structured-output agents (planner/reviewer) only: when True,
    # ``StructuredOutputAgent.handle`` validates the LLM's JSON reply against
    # the agent's schema and raises ``LLMBadResponse`` on mismatch. Defaults
    # to ``DEFAULT_VALIDATE_OUTPUT`` (off), preserving the raw pass-through
    # contract for existing environments.
    validate_output: bool = DEFAULT_VALIDATE_OUTPUT


DEFAULT_LOOP_MAX_STEPS: int = 1


DEFAULT_LOOP_STEP_TIMEOUT: float = 30.0


class LoopSettings(BaseModel):
    """Iterative control-loop parameters."""

    max_steps: int = DEFAULT_LOOP_MAX_STEPS
    step_timeout_seconds: float = DEFAULT_LOOP_STEP_TIMEOUT
