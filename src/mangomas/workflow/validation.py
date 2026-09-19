"""Static checks a graph's *shape* cannot express, run at load time.

Pydantic validates that a :class:`~mangomas.workflow.graph.WorkflowGraph` is
well-formed. It cannot validate the one thing that makes an acceptance predicate
*correct*: whether the predicate can actually read what the agent it grades
emits. Two defects slip through a schema-valid graph, and both were reachable
until this module existed.

**1. A text predicate over a structured agent is a self-report match.**
``ReviewerAgent`` emits a ``ReviewResult``; ``PlannerAgent`` an
``ExecutionPlan``. ``{"kind": "contains", "value": "approved"}`` on a loop over
``reviewer`` accepts ``{"verdict": "reject", "notes": "approved approach
rejected"}`` — the loop terminates on a substring of prose the reviewer itself
authored, inside a rejecting review. spec-0032 added ``json_field`` as the fix
and documented it; documentation is not a mechanism, so the wrong spelling stayed
loadable.

**2. A ``json_field`` path that addresses nothing can never accept.**
``{"kind": "json_field", "field": "pased", "equals": true}`` validates, compiles,
and returns ``False`` for every response, so the loop exhausts ``max_steps`` and
raises :class:`~mangomas.errors.MaxStepsExceeded`. That is correct *runtime*
behaviour — :func:`~mangomas.workflow.predicate.compile_predicate` is total by
design — but it makes a typo indistinguishable from a model that never converges.
The agent's schema is known at load time, so the typo is free to catch there.

Loop nodes only, deliberately
-----------------------------
``LoopNode.accept`` grades the output of ``LoopNode.agent`` — a known agent, so
its schema is knowable. ``BranchCase.when`` does **not**: the branch executor
evaluates it against the node's *input* content, the last threaded message
(``mangomas.workflow.nodes.branch._input_content``), which may be the original
request or the output of any upstream step. There is no agent to bind a branch
predicate to, and inferring the upstream producer would be wrong as often as
right — a branch can be a sequence's first step, or nested inside a ``fan_out``.
Guessing would produce false refusals, which is worse than no guard. So a text
predicate on ``branch.when`` stays legal, and correctly so: routing on substrings
of arbitrary threaded text is exactly what it is for.

Why the caller supplies the schema map
--------------------------------------
``mangomas.workflow`` is a pure-domain package: it compiles graphs down to public
dispatch calls addressed by agent *name* and never resolves an agent. It
therefore takes the structured-agent field map as **data** (see
``mangomas.composition.agents.STRUCTURED_AGENT_FIELDS``) rather than importing
``mangomas.agents``. The import-linter contracts do not forbid that import, so
the discipline is deliberate rather than enforced — stated here so a future
reader does not "simplify" it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, TypeAlias

from mangomas.errors import ConfigError
from mangomas.workflow.graph import LoopNode, iter_nodes
from mangomas.workflow.predicate import FIELD_PATH_SEPARATOR, TEXT_MATCH_KINDS

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.workflow.graph import WorkflowGraph
    from mangomas.workflow.predicate import PredicateSpec

logger = logging.getLogger(__name__)

#: Structured-log event for a completed validation pass.
_VALIDATED_EVENT: Final[str] = "workflow_structured_acceptance_validated"


@dataclass(frozen=True, slots=True)
class StructuredAgentSchema:
    """What acceptance validation needs to know about one agent's output schema.

    Deliberately *not* the Pydantic model. ``mangomas.workflow`` is pure-domain
    and takes this as plain data (built by ``mangomas.composition.agents``), so it
    carries no knowledge of ``mangomas.agents`` — see the module docstring.

    ``fields``
        Top-level property names. Membership answers "may a ``json_field``
        predicate address this path?".
    ``object_fields``
        The subset whose value is a JSON **object**. This is what makes a dotted
        path decidable: :func:`~mangomas.workflow.predicate.compile_predicate`'s
        resolver walks ``Mapping`` values only, so a path that continues past an
        array, a string or a number can never resolve — the predicate would return
        ``False`` for every response and the loop could only end in
        ``MaxStepsExceeded``. That is the same never-accepts class the
        first-segment check exists to catch, one level down.
    """

    fields: frozenset[str]
    object_fields: frozenset[str] = frozenset()


#: ``Mapping[agent name, :class:`StructuredAgentSchema`]`` — the shape every entry
#: point here takes. An empty mapping disables every rule, which is the default
#: and preserves pre-spec behaviour.
StructuredAgentFields: TypeAlias = Mapping[str, StructuredAgentSchema]


def _text_predicate_problem(agent: str, spec: PredicateSpec) -> str | None:
    """Return a message when *spec* text-matches a structured agent's output."""
    if spec.kind not in TEXT_MATCH_KINDS:
        return None
    return (
        f"loop over structured agent {agent!r} uses a {spec.kind!r} acceptance "
        f"predicate, which matches the agent's own serialised self-report. No "
        f"substring spelling is correct in both directions: a quoted needle "
        f"never matches compact JSON, and a quoteless one matches prose inside "
        f"the reply and accepts a rejecting result. Use "
        f'{{"kind": "json_field", "field": "<field>", ...}} to bind acceptance '
        f"to a parsed field."
    )


def _json_field_problem(
    agent: str, spec: PredicateSpec, schema: StructuredAgentSchema
) -> str | None:
    """Return a message when *spec*'s path cannot resolve against *schema*.

    Two rules, both about the same failure: a path that cannot resolve compiles to
    a closure returning ``False`` for every response, so the loop exhausts
    ``max_steps`` and a typo becomes indistinguishable from a model that never
    converges.

    1. The **first** segment must be a declared field.
    2. When the path has more than one segment, that first segment must be an
       **object**. The predicate resolver walks mappings only
       (``predicate._resolve``), so continuing past an array, string, number or
       boolean can never resolve. Neither shipped model has an object-valued
       field, so every dotted path over ``ExecutionPlan`` / ``ReviewResult`` is
       refused — including ``steps.0.action``, which reads plausible and is not.

    Segments beyond the second are **not** validated: doing so needs the nested
    model's own schema, which this carrier deliberately does not hold. Stated as a
    limit rather than implied — a wrong deeper segment still degrades to "not
    accepted" at run time, exactly as before.
    """
    if not spec.field:
        return None
    segments = spec.field.split(FIELD_PATH_SEPARATOR)
    first = segments[0]
    if first not in schema.fields:
        return (
            f"loop over structured agent {agent!r} accepts on field {spec.field!r}, "
            f"whose first segment {first!r} is not in the agent's schema. The "
            f"predicate would never match, so the loop could only ever end in "
            f"MaxStepsExceeded — indistinguishable from a model that does not "
            f"converge. Available fields: {sorted(schema.fields)}."
        )
    if len(segments) > 1 and first not in schema.object_fields:
        return (
            f"loop over structured agent {agent!r} accepts on dotted path "
            f"{spec.field!r}, but {first!r} is not an object in the agent's "
            f"schema. The predicate resolver walks mappings only, so this path "
            f"can never resolve and the loop could only ever end in "
            f"MaxStepsExceeded. Address {first!r} directly, or pick one of the "
            f"object-valued fields: {sorted(schema.object_fields) or 'none'}."
        )
    return None


def structured_acceptance_problems(
    graph: WorkflowGraph,
    structured_agents: StructuredAgentFields,
) -> list[str]:
    """Return every structured-acceptance problem in *graph*; ``[]`` when clean.

    Pure over its inputs and separate from the raising wrapper below so tests can
    assert on the findings themselves, and so a future caller that wants to report
    all problems rather than fail on the first has somewhere to hook in.
    """
    problems: list[str] = []
    for node in iter_nodes(graph.root):
        if not isinstance(node, LoopNode):
            continue
        schema = structured_agents.get(node.agent)
        if schema is None:
            continue
        problem = _text_predicate_problem(node.agent, node.accept) or _json_field_problem(
            node.agent, node.accept, schema
        )
        if problem is not None:
            problems.append(problem)
    return problems


def validate_structured_acceptance(
    graph: WorkflowGraph,
    structured_agents: StructuredAgentFields | None,
) -> None:
    """Raise :class:`~mangomas.errors.ConfigError` on a structured-acceptance defect.

    ``structured_agents=None`` (or empty) skips both rules and is the default, so
    every existing caller and every previously valid graph is unaffected. Reusing
    ``ConfigError`` keeps this at the boundary
    :func:`~mangomas.workflow.loader.load_workflow` already normalises to, so the
    API answers 400 and the CLI exits 2 with no route, DTO or error-table change.

    All problems are reported together: a graph with two bad loops should not
    require two round trips to fix.
    """
    if not structured_agents:
        return
    problems = structured_acceptance_problems(graph, structured_agents)
    if problems:
        raise ConfigError(f"invalid workflow graph {graph.name!r}: " + "; ".join(problems))
    logger.debug(
        "workflow structured-acceptance validation passed",
        extra={
            "event": _VALIDATED_EVENT,
            "graph_name": graph.name,
            "structured_agents": sorted(structured_agents),
        },
    )
