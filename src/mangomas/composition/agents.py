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

from pydantic import BaseModel

from mangomas.agents import ChatAgent, PlannerAgent, ReviewerAgent, SummarizeAgent, ToolAgent
from mangomas.agents._structured import SCHEMA_CLASS_ATTRIBUTE, StructuredOutputAgent
from mangomas.composition._registries import AgentFactory, agent_registry
from mangomas.errors import ConfigError
from mangomas.workflow.validation import StructuredAgentFields, StructuredAgentSchema

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable

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


#: JSON-Schema ``type`` marking a value the predicate resolver can walk into.
#: ``predicate._resolve`` traverses ``Mapping`` values only, so this is the one
#: type a dotted path may continue past.
_OBJECT_SCHEMA_TYPE: Final[str] = "object"


def _schema_of(agent_class: type[Agent], *, name: str) -> type[BaseModel]:
    """Return *agent_class*'s declared output model, or raise ``ConfigError``.

    Raises when a structured agent omits its schema declaration. Failing loudly is
    deliberate: skipping such an agent would silently drop its acceptance guard,
    which is the exact defect class this map exists to close.
    """
    schema = getattr(agent_class, SCHEMA_CLASS_ATTRIBUTE, None)
    if schema is None:
        raise ConfigError(
            f"agent {name!r} ({agent_class.__name__}) subclasses "
            f"StructuredOutputAgent but declares no "
            f"{SCHEMA_CLASS_ATTRIBUTE!r} class attribute, so its workflow "
            f"acceptance predicates cannot be validated. Add "
            f"`{SCHEMA_CLASS_ATTRIBUTE}: ClassVar[type[BaseModel]] = <Model>`."
        )
    return cast("type[BaseModel]", schema)


def describe_schema(model: type[BaseModel]) -> StructuredAgentSchema:
    """Summarise *model*'s JSON schema for workflow acceptance validation.

    Carries the top-level property names, plus which of them are **objects** —
    the only values a dotted predicate path may continue past, because
    ``predicate._resolve`` walks mappings alone. Derived from the live schema, so
    adding a field to ``ReviewResult`` widens what a predicate may address with no
    change here.

    A property reached through ``$ref`` (a nested model) reports no inline
    ``type``, so it is treated as an object: refusing it would be the damaging
    direction, and a wrong segment *inside* it still degrades to "not accepted" at
    run time as before.
    """
    properties: Mapping[str, Mapping[str, object]] = model.model_json_schema().get("properties", {})
    object_fields = {
        name
        for name, subschema in properties.items()
        if subschema.get("type", _OBJECT_SCHEMA_TYPE) == _OBJECT_SCHEMA_TYPE
    }
    return StructuredAgentSchema(
        fields=frozenset(properties),
        object_fields=frozenset(object_fields),
    )


def structured_agent_schemas(agent_classes: Mapping[str, type[Agent]]) -> StructuredAgentFields:
    """Return ``{agent name: schema summary}`` for the structured entries.

    Takes the name-to-**class** mapping so it serves both callers: the built-in
    table below, and :func:`structured_agent_schemas_for_instances`, which covers
    entry-point plugins.
    """
    return {
        name: describe_schema(_schema_of(agent_class, name=name))
        for name, agent_class in agent_classes.items()
        if isinstance(agent_class, type) and issubclass(agent_class, StructuredOutputAgent)
    }


def structured_agent_schemas_for_instances(
    agents: Iterable[Agent],
) -> StructuredAgentFields:
    """Return the schema map for *agents*, keyed by each agent's own ``name``.

    This is what closes the plugin gap. ``ensure_agent_plugins`` registers
    entry-point **factories** after this module imports, so a discovered
    ``StructuredOutputAgent`` is absent from :data:`DEFAULT_AGENTS` and would get
    neither acceptance guard. ``build_orchestrator`` already constructs every
    registered agent — plugins included — so deriving from those live instances
    covers them with no change to the plugin protocol and no extra construction.

    ``type(agent)`` rather than the instance: the schema is a ``ClassVar``, and
    reading it off the class keeps this a lookup rather than a second build.
    """
    return structured_agent_schemas({agent.name: type(agent) for agent in agents})


#: Structured **built-in** agents mapped to their output-schema summary.
#:
#: Consumed by ``mangomas.workflow.validation`` through ``load_workflow``'s
#: ``structured_agents`` keyword. Membership answers "is this agent structured?";
#: the value answers "may a ``json_field`` predicate address this path, and may it
#: continue past this segment?".
#:
#: Covers the **built-ins** only, because it is computed at import time.
#: Entry-point discovered agents register later, so ``build_orchestrator``
#: recomputes the full map from the live agents via
#: :func:`structured_agent_schemas_for_instances` and publishes it on
#: ``AgentContext.extras``; the API and CLI prefer that map and fall back to this
#: constant. See ``composition/builder.py``.
STRUCTURED_AGENT_FIELDS: Final[StructuredAgentFields] = structured_agent_schemas(DEFAULT_AGENTS)

#: ``AgentContext.extras`` key under which ``build_orchestrator`` publishes the
#: **plugin-inclusive** schema map. Named once here, following the
#: ``COGNITIVE_SINK_EXTRAS_KEY`` precedent, so the writer (``builder``) and the
#: readers (the API route and the CLI command) cannot disagree about the spelling.
STRUCTURED_AGENT_FIELDS_EXTRAS_KEY: Final[str] = "structured_agent_fields"


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
    "STRUCTURED_AGENT_FIELDS_EXTRAS_KEY",
    "_register_default_agents",
    "describe_schema",
    "structured_agent_schemas",
    "structured_agent_schemas_for_instances",
]
