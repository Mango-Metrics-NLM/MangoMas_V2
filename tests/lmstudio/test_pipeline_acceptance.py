"""LM Studio E2E — scenario 9: whole-pipeline acceptance loops (spec-0027).

The design point that makes this hardware- *and* model-independent: both
outcomes are decided by the acceptance function the test supplies, not by
anything the model says. An always-accept predicate stops after one pass on
any hardware with any model; a never-accept predicate exhausts the budget just
as reliably. The model's text never enters an assertion (spec-0029 R2.3).

What this confirms beyond the unit tests: that a real, slow, multi-stage
pipeline threads its final response back into the first stage and reports the
same ``metadata["loop"]`` block a single dispatch does — over real network
latency, where a re-injection bug would surface as a wrong step count rather
than an obvious error.

Skipped unless ``RUN_LMSTUDIO=1``.
"""

from __future__ import annotations

import logging

import pytest

from mangomas.core import AgentRequest, Message, Orchestrator
from mangomas.errors import MaxStepsExceeded

logger = logging.getLogger(__name__)

# Two stages of the same agent: enough to make it a pipeline, cheap enough to
# run twice against a local model.
_ROSTER = ["chat", "chat"]
_MAX_STEPS = 2
_PROMPT = "Give a one-sentence definition of unit testing."


def _request() -> AgentRequest:
    return AgentRequest(messages=[Message(role="user", content=_PROMPT)])


@pytest.mark.lmstudio
async def test_acceptance_on_the_first_pass_stops_the_loop(
    lmstudio_orchestrator: Orchestrator,
) -> None:
    """An always-true predicate ends after one pass, whatever the model said."""
    response = await lmstudio_orchestrator.dispatch_pipeline(
        _ROSTER, _request(), acceptance_fn=lambda _: True, max_steps=_MAX_STEPS
    )

    assert response.metadata["loop"] == {"steps_taken": 1, "accepted": True}
    assert response.content, "the pipeline must still return real content"


@pytest.mark.lmstudio
async def test_never_accepting_exhausts_the_budget(
    lmstudio_orchestrator: Orchestrator,
) -> None:
    """An always-false predicate raises ``MaxStepsExceeded`` naming the budget.

    The pair is the contract: without this, a pipeline that silently returned
    after one pass regardless of the predicate would pass the test above.
    """
    with pytest.raises(MaxStepsExceeded) as exc_info:
        await lmstudio_orchestrator.dispatch_pipeline(
            _ROSTER, _request(), acceptance_fn=lambda _: False, max_steps=_MAX_STEPS
        )

    assert exc_info.value.steps == _MAX_STEPS
    logger.info("Live pipeline exhausted its budget as expected", extra={"steps": _MAX_STEPS})


@pytest.mark.lmstudio
async def test_a_pipeline_without_acceptance_runs_once(
    lmstudio_orchestrator: Orchestrator,
) -> None:
    """Defaults-None is the pre-spec-0027 behaviour: a single pass, no loop.

    Spec-0027's backwards-compatibility claim, confirmed live.
    """
    response = await lmstudio_orchestrator.dispatch_pipeline(_ROSTER, _request())

    assert response.metadata["loop"]["steps_taken"] == 1
    assert response.content
