"""What ``mean_cost_usd`` actually measures — pinned before anything gates on it.

Until spec-0045 the answer was "what the dataset declared": ``Target.run``
returned a bare ``str``, so the ``AgentResponse`` and its ``metadata`` were
discarded at the seam, and ``cost_budget`` resolved explicit ``cost_usd`` →
declared token counts → output-character rate against the JSONL row alone. A
cost gate therefore fired on verbosity and was blind to a costlier model, a
price change or extra tool steps (D15 in
``docs/analysis/20260919-council-rejection-and-replan.md``).

The chain now, verified here rather than asserted in prose:

1. ``Target.run(request, *, orch) -> TargetOutcome`` carries the response
   metadata alongside the graded text (``eval/target.py``).
2. ``EvalRunner._score_row`` hands the scorer **both** mappings:
   ``ScorerContext.row_metadata`` (declared) and
   ``ScorerContext.response_metadata`` (produced by the run).
3. ``CostBudgetScorer`` trusts exactly one thing in the response metadata: the
   adapter-written ``usage`` block, and only when it counted at least one LLM
   call. That tier wins over every declared tier and is labelled
   ``measured_tokens``. A target that merely *writes* ``cost_usd`` or token keys
   into its own response metadata is not an adapter report and changes nothing.

So the figure is **measured when an adapter reports usage, declared otherwise**,
and the row's ``source`` says which. With a silent adapter the old limits still
hold: cost tracks reply length and is blind to the model the run used. These
tests pin both halves and must be updated deliberately if either changes.
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
from mangomas.eval.scorers.cost_budget import CostBudgetScorer, estimate_cost_usd, measured_usage
from mangomas.eval.target import Target, TargetOutcome
from tests.constants import (
    DEFAULT_AGENT_NAME,
    DEFAULT_EVAL_COST_USD_PER_1K_INPUT_TOKENS,
    DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_CHARS,
    DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_TOKENS,
    EVAL_COST_INPUT_TOKENS_METADATA_KEY,
    EVAL_COST_OUTPUT_TOKENS_METADATA_KEY,
    EVAL_COST_USD_METADATA_KEY,
    EVAL_USAGE_LLM_CALLS_KEY,
    EVAL_USAGE_METADATA_KEY,
)
from tests.fakes import FakeLLM, FakeRepository

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import AgentRequest

#: A cost no character-rate estimate could coincidentally produce, so its absence
#: from the report is proof the response metadata was not read.
_SENTINEL_COST_USD = 12_345.6789
#: An adapter-shaped usage report; the only response metadata the scorer trusts.
_MEASURED_INPUT_TOKENS = 1_200
_MEASURED_OUTPUT_TOKENS = 340

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


async def test_response_metadata_reaches_the_scorer_but_only_measured_usage_is_trusted() -> None:
    """spec-0045 closed D15(b): the seam carries response metadata — narrowly.

    A target that writes an explicit ``cost_usd`` and token keys into its own
    response metadata is still not heard: those keys describe a *claim*, not an
    adapter report. Only the ``usage`` block an LLM adapter accumulates (with a
    positive ``llm_calls``) is the measured tier.
    """

    class _ClaimingTarget:
        """Writes cost keys into the response — the shape a careless target might use."""

        name = DEFAULT_AGENT_NAME

        async def run(self, request: AgentRequest, *, orch: Orchestrator) -> TargetOutcome:
            response = await orch.dispatch(DEFAULT_AGENT_NAME, request)
            response.metadata[EVAL_COST_USD_METADATA_KEY] = _SENTINEL_COST_USD
            response.metadata[EVAL_COST_INPUT_TOKENS_METADATA_KEY] = 999_999
            response.metadata[EVAL_COST_OUTPUT_TOKENS_METADATA_KEY] = 999_999
            return TargetOutcome(content=response.content, metadata=dict(response.metadata))

    class _MeasuringTarget(_ClaimingTarget):
        """Carries the adapter-shaped usage block as well — this one is heard."""

        async def run(self, request: AgentRequest, *, orch: Orchestrator) -> TargetOutcome:
            outcome = await super().run(request, orch=orch)
            outcome.metadata[EVAL_USAGE_METADATA_KEY] = {
                EVAL_USAGE_LLM_CALLS_KEY: 1,
                EVAL_COST_INPUT_TOKENS_METADATA_KEY: _MEASURED_INPUT_TOKENS,
                EVAL_COST_OUTPUT_TOKENS_METADATA_KEY: _MEASURED_OUTPUT_TOKENS,
            }
            return outcome

    claiming = _ClaimingTarget()
    assert isinstance(claiming, Target)
    report = await EvalRunner(_orchestrator(_SHORT_REPLY), CostBudgetScorer()).run(
        [_row("r1")], target=claiming
    )
    assert report.mean_cost_usd is not None
    assert report.mean_cost_usd != pytest.approx(_SENTINEL_COST_USD)
    assert report.mean_cost_usd == await _mean_cost(_orchestrator(_SHORT_REPLY), [_row("r1")])
    assert report.rows[0].metadata["source"] == "output_chars"
    # The metadata did arrive — it is on the row — it just carried no measurement.
    assert EVAL_COST_USD_METADATA_KEY in report.rows[0].response_metadata

    measured = await EvalRunner(_orchestrator(_SHORT_REPLY), CostBudgetScorer()).run(
        [_row("r1")], target=_MeasuringTarget()
    )
    expected = (
        _MEASURED_INPUT_TOKENS / 1000 * DEFAULT_EVAL_COST_USD_PER_1K_INPUT_TOKENS
        + _MEASURED_OUTPUT_TOKENS / 1000 * DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_TOKENS
    )
    assert measured.mean_cost_usd == pytest.approx(expected)
    assert measured.rows[0].metadata["source"] == "measured_tokens"


def test_a_usage_block_with_no_counted_calls_is_not_a_measurement() -> None:
    """An adapter that never reported leaves ``llm_calls`` at 0 — that is silence, not zero cost."""
    cost, source = estimate_cost_usd(
        _SHORT_REPLY,
        metadata={},
        usd_per_1k_input_tokens=DEFAULT_EVAL_COST_USD_PER_1K_INPUT_TOKENS,
        usd_per_1k_output_tokens=DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_TOKENS,
        usd_per_1k_output_chars=DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_CHARS,
        response_metadata={
            EVAL_USAGE_METADATA_KEY: {
                EVAL_USAGE_LLM_CALLS_KEY: 0,
                EVAL_COST_INPUT_TOKENS_METADATA_KEY: 5,
            }
        },
    )
    assert source == "output_chars"
    assert cost == pytest.approx(
        len(_SHORT_REPLY) / 1000 * DEFAULT_EVAL_COST_USD_PER_1K_OUTPUT_CHARS
    )
    assert measured_usage({EVAL_USAGE_METADATA_KEY: "not a mapping"}) is None
    assert measured_usage(None) is None


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
