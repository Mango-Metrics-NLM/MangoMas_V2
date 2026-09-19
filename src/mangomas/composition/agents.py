"""Agent factory registration.

Seeds the default in-process agents into the agent registry.
Optional entry-point discovery can add to this registry later without
changing build_orchestrator().

The registration **table** below is the single source of truth for both jobs
this module does: registering factories, and deriving
:data:`STRUCTURED_AGENT_FIELDS` — the name-to-schema-fields map that
``mangomas.workflow`` uses to validate a graph's acceptance predicates at load
time. Deriving the second from the first is what stops a newly added structured
agent from silently shipping without its acceptance guard.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Final, TypeAlias, cast

from mangomas.agents import ChatAgent, PlannerAgent, ReviewerAgent, SummarizeAgent, ToolAgent
from mangomas.agents._structured import SCHEMA_CLASS_ATTRIBUTE, StructuredOutputAgent
from mangomas.composition._registries import AgentFactory, agent_registry
from mangomas.errors import ConfigError

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.config import AgentSettings
    from mangomas.core.agent import Agent

logger = logging.getLogger(__name__)


#: Every built-in agent, by the name it dispatches under. A table rather than a
#: sequence of ``register`` calls so the name-to-class mapping is *data* two
#: consumers can read: the registration loop below, and the structured-agent
#: derivation after it. Adding an agent here is the whole of step 2 of the
#: four-step extension pattern in ``CLAUDE.md``.
DEFAULT_AGENTS: Final[Mapping[str, type[Agent]]] = {
    "chat": ChatAgent,
    "summarize": SummarizeAgent,
    "tool": ToolAgent,
    "planner": PlannerAgent,
    "reviewer": ReviewerAgent,
}


#: The shared constructor contract of every built-in agent class.
#:
#: ``Agent`` is a protocol over ``handle``; it says nothing about ``__init__``, so
#: ``type[Agent]`` alone does not type ``cls(settings=...)``. The built-ins differ
#: in their other constructor parameters (``history_limit`` on ``summarize``,
#: ``max_tool_steps`` on ``tool``) and all of those default, so accepting
#: ``settings`` as a keyword is exactly the shared shape. The cast below asserts
#: it; ``tests/composition/test_agents.py`` pins it for every table entry, so the
#: assertion is mechanically checked rather than taken on trust.
_SettingsConstructor: TypeAlias = Callable[..., "Agent"]


def _agent_factory(agent_class: type[Agent]) -> AgentFactory:
    """Return a factory closing over *agent_class*.

    A named function rather than an inline ``lambda`` in the loop: a lambda would
    close over the loop *variable*, so every registered factory would build the
    last class in the table.
    """
    construct = cast("_SettingsConstructor", agent_class)

    def _build(settings: AgentSettings | None) -> Agent:
        return construct(settings=settings)

    return _build


def _structured_agent_fields() -> Mapping[str, frozenset[str]]:
    """Return ``{agent name: top-level schema field names}`` for structured agents.

    Derived from :data:`DEFAULT_AGENTS` via ``issubclass`` plus the declared
    :data:`~mangomas.agents._structured.SCHEMA_CLASS_ATTRIBUTE`, so a sixth
    structured agent is covered by adding it to the table alone.

    **Field names, not the model class.** ``mangomas.workflow`` is a pure-domain
    package that compiles graphs to dispatch calls by agent *name* and never
    resolves an agent; handing it a set of legal field names keeps it free of any
    knowledge of ``mangomas.agents`` (the layering contracts do not forbid that
    import, so the discipline has to be deliberate). The set is still derived
    from the live schema, so adding a field to ``ReviewResult`` widens what a
    predicate may address with no change here.

    Raises :class:`~mangomas.errors.ConfigError` when a structured agent omits
    its schema declaration. Failing at import is deliberate: skipping such an
    agent would silently drop its acceptance guard, which is the exact defect
    class this map exists to close.
    """
    fields: dict[str, frozenset[str]] = {}
    for name, agent_class in DEFAULT_AGENTS.items():
        if not issubclass(agent_class, StructuredOutputAgent):
            continue
        schema = getattr(agent_class, SCHEMA_CLASS_ATTRIBUTE, None)
        if schema is None:
            raise ConfigError(
                f"agent {name!r} ({agent_class.__name__}) subclasses "
                f"StructuredOutputAgent but declares no "
                f"{SCHEMA_CLASS_ATTRIBUTE!r} class attribute, so its workflow "
                f"acceptance predicates cannot be validated. Add "
                f"`{SCHEMA_CLASS_ATTRIBUTE}: ClassVar[type[BaseModel]] = <Model>`."
            )
        fields[name] = frozenset(schema.model_json_schema().get("properties", {}))
    return fields


#: Structured built-in agents mapped to their schema's **top-level** field names.
#:
#: Consumed by ``mangomas.workflow.validation`` through ``load_workflow``'s
#: ``structured_agents`` keyword. Membership answers "is this agent structured?";
#: the value answers "may a ``json_field`` predicate address this path?".
#:
#: Known limit: entry-point discovered agents (``MANGOMAS_DISCOVERY_ENABLED``)
#: register *after* this module imports and are not in :data:`DEFAULT_AGENTS`, so
#: a discovered structured agent gets no guard. Stated rather than implied.
STRUCTURED_AGENT_FIELDS: Final[Mapping[str, frozenset[str]]] = _structured_agent_fields()


def _register_default_agents() -> None:
    """Register the default in-process agents.

    Called during build_orchestrator() to seed the agent registry.
    """
    for name, agent_class in DEFAULT_AGENTS.items():
        agent_registry.register(name, _agent_factory(agent_class))
    logger.debug(
        "Default agents registered: %s (structured: %s)",
        ", ".join(DEFAULT_AGENTS),
        ", ".join(sorted(STRUCTURED_AGENT_FIELDS)) or "none",
    )


# Seed the default agents at import time (same as original composition.py)
_register_default_agents()

__all__ = [
    "DEFAULT_AGENTS",
    "STRUCTURED_AGENT_FIELDS",
    "_register_default_agents",
]
