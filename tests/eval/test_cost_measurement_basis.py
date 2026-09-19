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

#: A cost no character-rate estimate could coincidentally produce, so its absence
#: from the report is proof the response metadata was not read.
_SENTINEL_COST_USD = 12_345.6789

#: Two replies of deliberately different length, and nothing else different.
_SHORT_REPLY = "ok"
_LONG_REPLY = "ok" * 200

#: A model id that is not the default — names the notionally pricier model whose
#: dedicated client the override seam routes to.
_OTHER_MODEL = "some-other-and-far-pricier-model"

#: ``AgentContext.extras`` key ADR-0028 carries per-agent LLM clients under.
#: Restated here rather than imported because ``agents._prompt.resolve_llm`` reads
#: it as a literal; a test that imported a constant could pass while the resolver
#: looked somewhere else.
_AGENT_LLM_OVERRIDES_KEY = "agent_llm_overrides"


def _orchestrator(reply: str) -> Orchestrator:
    """One chat agent over a ``FakeLLM`` scripted to return *reply*."""
    ctx = AgentContext(llm=FakeLLM(reply=reply), repo=FakeRepository())
    orch = Orchestrator(ctx)
    orch.register(ChatAgent())
    return orch


def _orchestrator_with_model_override(reply: str) -> tuple[Orchestrator, FakeLLM, FakeLLM]:
    """Return an orchestrator whose agent genuinely resolves to an override client.

    ``AgentSettings(model_override=...)`` alone changes **nothing** at runtime: per
    ADR-0028 the model override is realised by a dedicated ``LLMClient`` that
    ``composition/builder.py`` puts in ``ctx.extras[agent_llm_overrides]``, and
    ``resolve_llm`` falls back to ``ctx.llm`` when that key lacks the agent. A
    fixture that set only the settings would therefore exercise no swap at all, and
    the blindness assertion below would hold vacuously.

    Returns both fakes so the caller can assert *which* client answered.
    """
    default_llm = FakeLLM(reply=reply)
    override_llm = FakeLLM(reply=reply)
    ctx = AgentContext(llm=default_llm, repo=FakeRepository())
    ctx.extras[_AGENT_LLM_OVERRIDES_KEY] = {DEFAULT_AGENT_NAME: override_llm}
    orch = Orchestrator(ctx)
    orch.register(ChatAgent(settings=AgentSettings(model_override=_OTHER_MODEL)))
    return orch, default_llm, override_llm


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
    """The same reply costs the same even when a *different client* answered.

    The swap is made real and then asserted: the override client is the one that
    received the call, and the default client was never touched. Only then does
    equal cost mean anything — no model identity or token count reaches the
    scorer, so a cost gate cannot catch a model-swap regression.
    """
    baseline = await _mean_cost(_orchestrator(_SHORT_REPLY), [_row("r1")])

    orch, default_llm, override_llm = _orchestrator_with_model_override(_SHORT_REPLY)
    overridden = await _mean_cost(orch, [_row("r1")])

    # The swap actually happened — without this the equality below is vacuous.
    assert override_llm.calls, "the override client was never called"
    assert not default_llm.calls, "the default client answered; no swap occurred"
    assert baseline == overridden


async def test_response_metadata_never_reaches_the_scorer() -> None:
    """The structural cause: ``Target.run`` returns ``str``, discarding the response.

    A target that attaches token counts to its ``AgentResponse`` still cannot get
    them to the scorer — the signature has no channel for them. If this test ever
    fails, the ``Target`` seam grew one (D15 option b), and the module docstring
    plus ``docs/eval/harness.md`` must be updated with it.
    """

    class _MetadataAttachingTarget:
        """A target that really does report usage, and is still not heard."""

        name = DEFAULT_AGENT_NAME

        async def run(self, request: AgentRequest, *, orch: Orchestrator) -> str:
            response = await orch.dispatch(DEFAULT_AGENT_NAME, request)
            # A real adapter would write usage here, so write it: an explicit
            # cost_usd (the scorer's *highest*-precedence tier) plus token counts.
            # If any of this reached the scorer the cost would be _SENTINEL_COST_USD.
            response.metadata[EVAL_COST_USD_METADATA_KEY] = _SENTINEL_COST_USD
            response.metadata[EVAL_COST_INPUT_TOKENS_METADATA_KEY] = 999_999
            response.metadata[EVAL_COST_OUTPUT_TOKENS_METADATA_KEY] = 999_999
            return response.content

    target = _MetadataAttachingTarget()
    assert isinstance(target, Target)

    orch = _orchestrator(_SHORT_REPLY)
    report = await EvalRunner(orch, CostBudgetScorer()).run([_row("r1")], target=target)
    assert report.mean_cost_usd is not None

    # The sentinel is nowhere: had the response metadata reached the scorer, its
    # explicit-cost tier would have won outright.
    assert report.mean_cost_usd != pytest.approx(_SENTINEL_COST_USD)
    # And the figure is identical to a plain agent target's — the attempt to
    # report usage changed nothing at all.
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
