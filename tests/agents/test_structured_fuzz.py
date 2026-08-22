"""Hypothesis fuzz for ``StructuredOutputAgent.parse`` — totality of the contract.

Import-guarded like the repo's other fuzz files (``hypothesis`` is an optional
dev dependency): the whole module skips when it is absent.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest
from pydantic import BaseModel

from mangomas.agents.planner import ExecutionPlan, PlannerAgent
from mangomas.errors import LLMBadResponse

_HypothesisDecorator = Callable[[Callable[..., Any]], Callable[..., Any]]
_hypothesis = pytest.importorskip("hypothesis")
given = cast(Callable[..., _HypothesisDecorator], _hypothesis.given)
settings = cast(Callable[..., _HypothesisDecorator], _hypothesis.settings)
st: Any = pytest.importorskip("hypothesis.strategies")


@settings(max_examples=200, deadline=None)
@given(st.text())
def test_parse_raises_only_llm_bad_response_or_returns_model(content: str) -> None:
    """Over arbitrary text, ``parse`` either raises ``LLMBadResponse`` or
    returns a schema instance — never any other exception, never ``None``."""
    agent = PlannerAgent()
    try:
        result = agent.parse(content)
    except LLMBadResponse:
        return
    assert isinstance(result, BaseModel)
    assert isinstance(result, ExecutionPlan)
