"""Extras keys and join-field names for the cognitive producer.

This module must not import ``mango_contracts`` so planner/reviewer ``handle``
can read the extras key when the plugin is off without loading the envelope.
"""

from __future__ import annotations

# Attached on AgentContext.extras when MANGOMAS_SIGNAL__ENABLED=true.
# Avoid a new AgentContext field — core/agent.py is a protected path.
COGNITIVE_SINK_EXTRAS_KEY: str = "cognitive_sink"


COGNITIVE_SETTINGS_EXTRAS_KEY: str = "cognitive_settings"


# Optional AgentRequest.metadata keys (UUID strings). When absent the producer
# mints fresh UUIDs — identity for the envelope, never a capability grant.
METADATA_RUN_ID: str = "run_id"


METADATA_TASK_ID: str = "task_id"


# Additive OTel GenAI alias (Development conventions; default-off).
GENAI_INVOKE_AGENT_SPAN: str = "gen_ai.invoke_agent"


# Pinned in spec-0030: conventions were still Development when this landed.
# Do not treat this as a frozen semconv version — aliases are additive.
GENAI_SEMCONV_STATUS: str = "development-2026-09"


# Attribute names / values for the optional GenAI alias. Live spans stay
# ``orchestrator.*`` / ``harness.agent_invoke``; this helper never replaces them.
GENAI_ATTR_OPERATION_NAME: str = "gen_ai.operation.name"


GENAI_ATTR_AGENT_NAME: str = "gen_ai.agent.name"


GENAI_ATTR_SYSTEM: str = "gen_ai.system"


GENAI_ATTR_SEMCONV_STATUS: str = "gen_ai.semconv.status"


GENAI_OPERATION_INVOKE_AGENT: str = "invoke_agent"


GENAI_SYSTEM_NAME: str = "mangomas"


# Default JSONL basename under ``SignalSettings.dir``. Not a Settings field:
# renaming the file is a producer-side choice, not an operator tunable.
JSONL_FILENAME: str = "signals.jsonl"


# Fallback payload text when planner JSON is missing or empty. Shared with
# tests so a wording change cannot silently desync assertions.
UNPARSED_PLANNER_STEP: str = "unparsed planner output"


UNPARSED_GOAL: str = "unparsed"
