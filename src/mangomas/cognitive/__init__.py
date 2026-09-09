"""Cognitive-plane producer (opt-in; spec-0030 / ADR-0029).

Emits ``CognitiveSignal`` 1.1.0 records for the sibling Code Agent Harness.
This package proposes and observes; it does not classify, authorize, or
broker. Default-OFF via ``MANGOMAS_SIGNAL__ENABLED``.

``__init__`` stays free of ``mango_contracts`` so a flag-off ``handle`` that
only reads :data:`COGNITIVE_SINK_EXTRAS_KEY` does not import the envelope.
Sink and producer live in submodules imported when the plugin is enabled.
"""

from __future__ import annotations

from mangomas.cognitive.constants import (
    COGNITIVE_SETTINGS_EXTRAS_KEY as COGNITIVE_SETTINGS_EXTRAS_KEY,
)
from mangomas.cognitive.constants import (
    COGNITIVE_SINK_EXTRAS_KEY as COGNITIVE_SINK_EXTRAS_KEY,
)
from mangomas.cognitive.constants import (
    GENAI_INVOKE_AGENT_SPAN as GENAI_INVOKE_AGENT_SPAN,
)
from mangomas.cognitive.constants import (
    GENAI_SEMCONV_STATUS as GENAI_SEMCONV_STATUS,
)
from mangomas.cognitive.constants import (
    JSONL_FILENAME as JSONL_FILENAME,
)
from mangomas.cognitive.constants import (
    METADATA_RUN_ID as METADATA_RUN_ID,
)
from mangomas.cognitive.constants import (
    METADATA_TASK_ID as METADATA_TASK_ID,
)
from mangomas.cognitive.constants import (
    UNPARSED_GOAL as UNPARSED_GOAL,
)
from mangomas.cognitive.constants import (
    UNPARSED_PLANNER_STEP as UNPARSED_PLANNER_STEP,
)
from mangomas.cognitive.roles import (
    FORBIDDEN_HARNESS_ROLES as FORBIDDEN_HARNESS_ROLES,
)
from mangomas.cognitive.roles import (
    HARNESS_OBSERVATION_ROLES as HARNESS_OBSERVATION_ROLES,
)
from mangomas.cognitive.roles import (
    UNMAPPED_OBSERVATION_AGENTS as UNMAPPED_OBSERVATION_AGENTS,
)
from mangomas.cognitive.roles import (
    UnknownAgentRoleError as UnknownAgentRoleError,
)
from mangomas.cognitive.roles import (
    UnmappedToolAgentError as UnmappedToolAgentError,
)
from mangomas.cognitive.roles import (
    harness_role_for_agent as harness_role_for_agent,
)

__all__ = [
    "COGNITIVE_SETTINGS_EXTRAS_KEY",
    "COGNITIVE_SINK_EXTRAS_KEY",
    "FORBIDDEN_HARNESS_ROLES",
    "GENAI_INVOKE_AGENT_SPAN",
    "GENAI_SEMCONV_STATUS",
    "HARNESS_OBSERVATION_ROLES",
    "JSONL_FILENAME",
    "METADATA_RUN_ID",
    "METADATA_TASK_ID",
    "UNMAPPED_OBSERVATION_AGENTS",
    "UNPARSED_GOAL",
    "UNPARSED_PLANNER_STEP",
    "UnknownAgentRoleError",
    "UnmappedToolAgentError",
    "harness_role_for_agent",
]
