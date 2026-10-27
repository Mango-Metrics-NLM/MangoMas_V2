"""Property tests: ``compile_predicate`` closures are total.

``compile_predicate``'s contract is that the returned closure **never raises**
— unparseable content, a non-object document, a path that runs off, an
integer too wide for a double — all are "not accepted", never an exception
out of the acceptance loop. The unit tests pin the three inputs that once
broke that promise; these state the invariant over arbitrary text and
arbitrary JSON documents, with the known breakers kept as ``@example``s so
the regression is checked on every run, not only when the search finds it.
"""

from __future__ import annotations

import json
from typing import Any

from hypothesis import example, given
from hypothesis import strategies as st

from mangomas.core import AgentResponse
from mangomas.workflow.predicate import PredicateSpec, compile_predicate
from tests.constants import REVIEW_SCORE_AT_LEAST, REVIEW_SCORE_FIELD, WORKFLOW_LOOP_SENTINEL

_PATH = f"{REVIEW_SCORE_FIELD}.inner"
_json_scalars = st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text()
_json_documents = st.recursive(
    _json_scalars,
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(), children, max_size=4)
    ),
    max_leaves=12,
)
_WIDER_THAN_A_DOUBLE = json.dumps({REVIEW_SCORE_FIELD: {"inner": 10**400}})

_SPECS = (
    PredicateSpec(kind="json_field", field=_PATH, at_least=REVIEW_SCORE_AT_LEAST),
    PredicateSpec(kind="json_field", field=_PATH, at_most=REVIEW_SCORE_AT_LEAST),
    PredicateSpec(kind="json_field", field=_PATH, equals=True),
    PredicateSpec(kind="contains", value=WORKFLOW_LOOP_SENTINEL),
    PredicateSpec(kind="regex", value=r"^\s*done", flags=["ignorecase"]),
)
_COMPILED = tuple(compile_predicate(spec) for spec in _SPECS)


def _resp(content: str) -> AgentResponse:
    return AgentResponse(content=content, agent="t")


@given(content=st.text(max_size=400))
@example(content=_WIDER_THAN_A_DOUBLE)
@example(content="{")
@example(content='{"score": NaN}')
def test_every_closure_is_total_over_arbitrary_text(content: str) -> None:
    for fn in _COMPILED:
        assert isinstance(fn(_resp(content)), bool)


@given(document=_json_documents)
@example(document={REVIEW_SCORE_FIELD: {"inner": 10**400}})
@example(document={REVIEW_SCORE_FIELD: {"inner": True}})
@example(document={REVIEW_SCORE_FIELD: []})
def test_every_closure_is_total_over_arbitrary_json(document: Any) -> None:
    content = json.dumps(document)
    for fn in _COMPILED:
        assert isinstance(fn(_resp(content)), bool)


@given(value=st.integers())
def test_a_boolean_at_the_path_never_satisfies_a_numeric_threshold(value: int) -> None:
    """spec-0032 R4 over every integer threshold, not one hand-picked one."""
    fn = compile_predicate(PredicateSpec(kind="json_field", field=_PATH, at_least=value))
    assert fn(_resp(json.dumps({REVIEW_SCORE_FIELD: {"inner": True}}))) is False
