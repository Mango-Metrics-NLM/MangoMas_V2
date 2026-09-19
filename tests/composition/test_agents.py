"""The built-in agent table, and the structured-agent map derived from it.

``composition/agents.py`` turned five ``register`` calls into a table so the
name-to-class mapping is data two consumers read: the registration loop, and
:data:`~mangomas.composition.agents.STRUCTURED_AGENT_FIELDS`, which
``mangomas.workflow`` uses to validate a graph's acceptance predicates.

Three things need pinning, because each is an assumption the table makes rather
than a property the type system gives:

* every entry really is constructible from ``settings`` alone (the module casts
  to assert this, since the ``Agent`` protocol describes ``handle``, not
  ``__init__``);
* the registered factories are distinct — the classic late-binding bug in a loop
  of lambdas registers the *last* class five times, and every agent would still
  resolve, just to the wrong implementation;
* the structured map is derived, so a sixth structured agent is covered by
  adding it to the table and nothing else.
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from pydantic import BaseModel

from mangomas.agents._structured import SCHEMA_CLASS_ATTRIBUTE, StructuredOutputAgent
from mangomas.composition import agent_registry
from mangomas.composition.agents import (
    DEFAULT_AGENTS,
    STRUCTURED_AGENT_FIELDS,
    _agent_factory,
    _structured_agent_fields,
)
from mangomas.errors import ConfigError
from tests.constants import (
    DEFAULT_AGENT_NAME,
    PLANNER_AGENT_NAME,
    REVIEW_PASSED_FIELD,
    REVIEWER_AGENT_NAME,
)


def test_the_table_and_the_registry_agree() -> None:
    """Every table entry is registered, under its own name."""
    assert set(DEFAULT_AGENTS) <= set(agent_registry.available())


@pytest.mark.parametrize("name", sorted(DEFAULT_AGENTS))
def test_every_entry_is_constructible_from_settings_alone(name: str) -> None:
    """The cast in ``_agent_factory`` asserts this; this is what checks it.

    ``Agent`` is a protocol over ``handle`` and says nothing about ``__init__``.
    The built-ins differ in their other constructor parameters (``history_limit``
    on ``summarize``, ``max_tool_steps`` on ``tool``) and all of those default, so
    the shared contract is exactly "accepts ``settings`` as a keyword". An agent
    added to the table without that shape would fail here rather than at runtime
    on first dispatch.
    """
    agent = _agent_factory(DEFAULT_AGENTS[name])(None)
    assert agent.name == name


def test_registered_factories_are_distinct() -> None:
    """Guard the late-binding trap: a lambda in the loop builds the last class.

    Every name would still resolve to *an* agent, so a smoke test that only
    checks "five agents registered" passes while four of them are wrong.
    """
    built = {name: type(agent_registry.get(name)(None)).__name__ for name in DEFAULT_AGENTS}
    assert len(set(built.values())) == len(DEFAULT_AGENTS), built
    assert built == {name: cls.__name__ for name, cls in DEFAULT_AGENTS.items()}


# ── the derived structured map ────────────────────────────────────────────────


def test_only_structured_agents_appear() -> None:
    """``chat``/``summarize``/``tool`` emit prose and must not be in the map.

    A map that included them would make every text predicate over a plain agent
    a load error — the guard's most damaging false positive.
    """
    assert set(STRUCTURED_AGENT_FIELDS) == {PLANNER_AGENT_NAME, REVIEWER_AGENT_NAME}
    assert DEFAULT_AGENT_NAME not in STRUCTURED_AGENT_FIELDS


def test_every_structured_agent_in_the_table_is_covered() -> None:
    """Derivation, not enumeration: the map must follow ``issubclass``."""
    expected = {
        name
        for name, cls in DEFAULT_AGENTS.items()
        if isinstance(cls, type) and issubclass(cls, StructuredOutputAgent)
    }
    assert set(STRUCTURED_AGENT_FIELDS) == expected


def test_fields_come_from_the_live_schema() -> None:
    """The values are the schema's top-level properties, read at import.

    Pinned against the model rather than a literal list, so adding a field to
    ``ReviewResult`` widens what a predicate may address with no test edit — and
    a map built from the wrong part of the JSON schema (``$defs`` rather than
    ``properties``) fails here.
    """
    for name, fields in STRUCTURED_AGENT_FIELDS.items():
        schema = getattr(DEFAULT_AGENTS[name], SCHEMA_CLASS_ATTRIBUTE)
        assert fields == frozenset(schema.model_json_schema()["properties"])
    assert REVIEW_PASSED_FIELD in STRUCTURED_AGENT_FIELDS[REVIEWER_AGENT_NAME]


def test_the_map_is_immutable_per_entry() -> None:
    """``frozenset`` values: a consumer cannot widen the guard by mutating them."""
    for fields in STRUCTURED_AGENT_FIELDS.values():
        assert isinstance(fields, frozenset)


def test_a_structured_agent_without_a_schema_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure mode the derivation must not have: a silently unguarded agent.

    Skipping such an agent would drop its acceptance guard without a word, which
    is the exact defect class this map closes. So it raises, and at composition
    import — loud in CI rather than shipped as a hole.
    """

    class _SchemalessAgent(StructuredOutputAgent):
        """A structured agent whose author forgot the class attribute."""

    monkeypatch.setitem(DEFAULT_AGENTS, "schemaless", _SchemalessAgent)
    with pytest.raises(ConfigError, match=SCHEMA_CLASS_ATTRIBUTE):
        _structured_agent_fields()


def test_a_new_structured_agent_is_covered_by_the_table_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of deriving: adding to the table is the whole of the work.

    The two-sided partner of the test above — omitting the schema fails, and
    declaring it succeeds with no edit to the derivation.
    """

    class _Verdict(BaseModel):
        accepted: bool
        rationale: str

    class _SixthAgent(StructuredOutputAgent):
        schema: ClassVar[type[BaseModel]] = _Verdict

    monkeypatch.setitem(DEFAULT_AGENTS, "sixth", _SixthAgent)
    derived = _structured_agent_fields()
    assert derived["sixth"] == frozenset({"accepted", "rationale"})
