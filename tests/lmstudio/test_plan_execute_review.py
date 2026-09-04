"""LM Studio E2E — scenario 10: the shipped graph, validated, against a real model.

**This is the one scenario whose result depends on model capability, and the
plan says so rather than hiding it.** Every other live scenario is structural:
it holds for any model that answers at all. This one requires the model to
emit JSON conforming to ``ExecutionPlan`` and ``ReviewResult`` when asked. A
model that cannot fails it on a GPU exactly as on a CPU — the failure is a
typed ``LLMBadResponse``, and that *is* the finding: it says this deployment's
model cannot drive the advertised pipeline with validation on.

The oracle is still structural (spec-0029 R2.3): the reply must *parse* as the
schema. Nothing asserts what the plan or the review says. ``temperature=0``
makes the run repeatable on one machine without making the assertion depend on
determinism.

Skipped unless ``RUN_LMSTUDIO=1``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from mangomas.agents.reviewer import ReviewResult
from mangomas.composition import build_orchestrator
from mangomas.config import AgentSettings
from mangomas.core import AgentRequest, Message
from mangomas.errors import LLMBadResponse
from mangomas.workflow import execute_workflow, load_workflow
from tests.constants import (
    PLAN_EXECUTE_REVIEW_AGENTS,
    PLAN_EXECUTE_REVIEW_GRAPH_RELPATH,
)
from tests.lmstudio.conftest import make_lmstudio_settings, orchestrator_cleanup

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GRAPH_PATH = _REPO_ROOT / PLAN_EXECUTE_REVIEW_GRAPH_RELPATH
_GOAL = "Add a health check endpoint to a small web service."
# Structured output is a following-instructions task, not a creative one:
# sampling only adds ways to emit invalid JSON.
_DETERMINISTIC = 0.0


def _validated_agents() -> dict[str, AgentSettings]:
    """Planner and reviewer with schema validation on, sampling off."""
    return {
        "planner": AgentSettings(validate_output=True, temperature=_DETERMINISTIC),
        "reviewer": AgentSettings(validate_output=True, temperature=_DETERMINISTIC),
        "tool": AgentSettings(temperature=_DETERMINISTIC),
    }


@pytest.mark.lmstudio
async def test_shipped_graph_completes_with_validation_on(
    lmstudio_base_url: str,
    lmstudio_model: str,
) -> None:
    """The advertised planner → tool → reviewer pipeline, end to end, validated.

    On failure, read the raised ``LLMBadResponse`` as a statement about the
    configured model rather than about this repository: the graph, the agents
    and the validation flag are all exercised by tier-1 flows against a fake,
    so a failure here isolates to the model's structured-output ability.
    """
    settings = make_lmstudio_settings(lmstudio_base_url, lmstudio_model, agents=_validated_agents())
    orch = build_orchestrator(settings)
    graph = load_workflow(str(_GRAPH_PATH))
    request = AgentRequest(messages=[Message(role="user", content=_GOAL)])

    async with orchestrator_cleanup(orch):
        try:
            response = await execute_workflow(graph, request, orch=orch)
        except LLMBadResponse:
            logger.exception(
                "Model could not satisfy the structured-output schema",
                extra={"model": lmstudio_model},
            )
            raise

    assert response.agent == PLAN_EXECUTE_REVIEW_AGENTS[-1]
    parsed = ReviewResult.model_validate_json(response.content)
    # Field-level structure only — never the review's verdict or wording.
    assert isinstance(parsed.passed, bool)
    assert isinstance(parsed.feedback, str)
    logger.info(
        "Shipped graph completed with validation on",
        extra={"model": lmstudio_model, "review_passed": parsed.passed},
    )
