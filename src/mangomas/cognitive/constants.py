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
