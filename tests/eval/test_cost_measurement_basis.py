"""What ``mean_cost_usd`` actually measures — pinned before anything gates on it.

``MANGOMAS_EVAL__MAX_MEAN_COST_USD``, ``EvalReport.mean_cost_usd`` and the
``cost_budget`` scorer all exist and are tested. None of them observes what a run
actually consumed, and nothing said so.

The chain, verified here rather than asserted in prose:

1. ``Target.run(request, *, orch) -> str`` (``eval/target.py``) returns a
   **string**. ``AgentTarget.run`` awaits ``orch.dispatch`` and returns
   ``response.content``, so the ``AgentResponse`` — and every field on it,
   ``metadata`` included — is discarded at that boundary.
2. ``EvalRunner._score_row`` builds ``ScorerContext(row_metadata=dict(row.metadata))``:
   the **dataset row's** metadata, i.e. what the JSONL file declared.
3. ``CostBudgetScorer`` reads that mapping, so its precedence — explicit
   ``cost_usd`` → token counts → output-character rate — resolves against
   declared inputs, never measured ones.

The consequence, and the reason this module exists: **a cost gate fires on
verbosity.** It cannot see a switch to a costlier model via ``MODEL_OVERRIDE``, a
price change per token, or extra tool steps. That is a real limit of a real
design — ``tests/eval/fixtures/cost_controlled_v1.jsonl`` hand-declares token
counts on purpose, giving a fixed-budget cohort for comparing targets — but it
was undocumented and unpinned, so a threshold could be published over a number
that does not mean what its name says.

D15 in ``docs/analysis/20260919-council-rejection-and-replan.md`` decides whether
to keep declared cost or measure it. These tests are what make that decision
informed; they assert the *current* contract and must be updated deliberately if
it changes, not quietly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mangomas.agents import ChatAgent
from mangomas.config import AgentSettings
from mangomas.core import AgentContext, Orchestrator
from mangomas.core.agent import Message
from mangomas.eval import EvalRunner
from mangomas.eval.dataset import DatasetRow
from mangomas.eval.scorers.cost_budget import CostBudgetScorer, estimate_cost_usd
from mangomas.eval.target import Target
from tests.constants import (
    DEFAULT_AGENT_NAME,
    DEFAULT_EVAL_COST_USD_PER_1K_INPUT_TOKENS,
    DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_CHARS,
    DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_TOKENS,
    EVAL_COST_INPUT_TOKENS_METADATA_KEY,
    EVAL_COST_OUTPUT_TOKENS_METADATA_KEY,
    EVAL_COST_USD_METADATA_KEY,
)
from tests.fakes import FakeLLM, FakeRepository

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import AgentRequest

#: Two replies of deliberately different length, and nothing else different.
_SHORT_REPLY = "ok"
_LONG_REPLY = "ok" * 200

#: A model id that is not the default — used to show the cost is unmoved by it.
_OTHER_MODEL = "some-other-and-far-pricier-model"


def _orchestrator(reply: str, *, model_override: str | None = None) -> Orchestrator:
    """One chat agent over a ``FakeLLM`` scripted to return *reply*."""
    ctx = AgentContext(llm=FakeLLM(reply=reply), repo=FakeRepository())
    orch = Orchestrator(ctx)
    settings = AgentSettings(model_override=model_override) if model_override else None
    orch.register(ChatAgent(settings=settings))
    return orch


def _row(row_id: str, *, metadata: dict[str, object] | None = None) -> DatasetRow:
    return DatasetRow(
        id=row_id,
        messages=[Message(role="user", content="anything")],
        expected="anything",
        metadata=metadata or {},
    )


async def _mean_cost(orch: Orchestrator, rows: list[DatasetRow]) -> float | None:
    report = await EvalRunner(orch, CostBudgetScorer()).run(rows, DEFAULT_AGENT_NAME)
    return report.mean_cost_usd


# ── the contract: cost tracks reply length, and nothing else ──────────────────


async def test_cost_tracks_reply_length_when_nothing_is_declared() -> None:
    """With no declared counts, cost is characters x a rate — so length moves it.

    This is the positive half: the number is not inert, it simply measures
    something other than spend.
    """
    short = await _mean_cost(_orchestrator(_SHORT_REPLY), [_row("r1")])
    long = await _mean_cost(_orchestrator(_LONG_REPLY), [_row("r1")])
    assert short is not None and long is not None
    assert long > short


async def test_cost_is_blind_to_the_model_the_run_used() -> None:
    """The same reply costs the same under a different (notionally pricier) model.

    ``MODEL_OVERRIDE`` changes which model answers; it cannot change
    ``mean_cost_usd``, because no model identity or token count reaches the
    scorer. A cost gate therefore cannot catch a model-swap regression.
    """
    baseline = await _mean_cost(_orchestrator(_SHORT_REPLY), [_row("r1")])
    overridden = await _mean_cost(
        _orchestrator(_SHORT_REPLY, model_override=_OTHER_MODEL), [_row("r1")]
    )
    assert baseline == overridden


async def test_response_metadata_never_reaches_the_scorer() -> None:
    """The structural cause: ``Target.run`` returns ``str``, discarding the response.

    A target that attaches token counts to its ``AgentResponse`` still cannot get
    them to the scorer — the signature has no channel for them. If this test ever
    fails, the ``Target`` seam grew one (D15 option b), and the module docstring
    plus ``docs/eval/harness.md`` must be updated with it.
    """

    class _MetadataAttachingTarget:
        """A target that *tries* to report usage and structurally cannot."""

        name = DEFAULT_AGENT_NAME

        async def run(self, request: AgentRequest, *, orch: Orchestrator) -> str:
            response = await orch.dispatch(DEFAULT_AGENT_NAME, request)
            # A real adapter would put usage here. It goes nowhere: the return
            # type is ``str``, so only ``content`` survives this boundary.
            assert isinstance(response.metadata, dict)
            return response.content

    target = _MetadataAttachingTarget()
    assert isinstance(target, Target)

    orch = _orchestrator(_SHORT_REPLY)
    report = await EvalRunner(orch, CostBudgetScorer()).run([_row("r1")], target=target)
    assert report.mean_cost_usd is not None
    # Identical to the plain agent target: the attempt changed nothing.
    assert report.mean_cost_usd == await _mean_cost(_orchestrator(_SHORT_REPLY), [_row("r1")])


async def test_declared_token_counts_do_move_the_cost() -> None:
    """The supported way to make cost meaningful: declare it in the dataset.

    This is what ``cost_controlled_v1.jsonl`` does, and why that design is sound
    for comparing targets at a fixed budget. It is also why the number is a
    property of the *dataset*, not of the run.
    """
    declared = await _mean_cost(
        _orchestrator(_SHORT_REPLY),
        [
            _row(
                "r1",
                metadata={
                    EVAL_COST_INPUT_TOKENS_METADATA_KEY: 1000,
                    EVAL_COST_OUTPUT_TOKENS_METADATA_KEY: 1000,
                },
            )
        ],
    )
    plain = await _mean_cost(_orchestrator(_SHORT_REPLY), [_row("r1")])
    assert declared is not None and plain is not None
    assert declared != plain
    expected = (
        DEFAULT_EVAL_COST_USD_PER_1K_INPUT_TOKENS + DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_TOKENS
    )
    assert declared == pytest.approx(expected)


# ── the precedence, stated directly ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("metadata", "expected_source"),
    [
        ({EVAL_COST_USD_METADATA_KEY: 1.25}, "explicit"),
        ({EVAL_COST_OUTPUT_TOKENS_METADATA_KEY: 10}, "tokens"),
        ({}, "output_chars"),
    ],
)
def test_the_fall_through_order_is_explicit_then_tokens_then_characters(
    metadata: dict[str, object], expected_source: str
) -> None:
    """Pin the tier actually used, so "which basis?" is answerable per row.

    The scorer already returns this ``source`` in its metadata. Nothing aggregates
    it onto the report, which is why a reader of ``mean_cost_usd`` alone cannot
    tell whether it came from declared spend or a character count — the gap D1a
    would close.
    """
    _, source = estimate_cost_usd(
        _SHORT_REPLY,
        metadata=metadata,
        usd_per_1k_input_tokens=DEFAULT_EVAL_COST_USD_PER_1K_INPUT_TOKENS,
        usd_per_1k_output_tokens=DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_TOKENS,
        usd_per_1k_output_chars=DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_CHARS,
    )
    assert source == expected_source
