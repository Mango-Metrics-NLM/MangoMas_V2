"""Hand-computed fixtures for the bake-off metrics and decision rule (spec-0035 R12).

Default suite, no marker: ``metrics`` is pure, so the rule that decides whether
structure-aware chunking ships is proven here on every push, long before any
live bake-off runs.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.rag.bakeoff.metrics import (
    DECISION_ADOPT,
    DECISION_INCONCLUSIVE,
    DECISION_REJECT,
    DEFAULT_MAX_STRATUM_REGRESSION,
    BootstrapResult,
    cluster_bootstrap_delta,
    context_precision_at_k,
    decide,
    mrr_at_k,
    ndcg_at_k,
    parse_recall,
    recall_at_k,
    span_hit,
)

# Relevant chunks sit at ranks 2 and 4; rank 2 differs from its span only in
# case, rank 4 only in whitespace — both must still count under normalisation.
RANKED = ["alpha beta", "gamma DELTA", "epsilon", "zeta \n  eta"]
SPANS = ["delta", "zeta eta"]

RankingMetric = Callable[[Sequence[str], Sequence[str], int], float]
RANKING_METRICS: list[RankingMetric] = [recall_at_k, mrr_at_k, ndcg_at_k, context_precision_at_k]


# ── span_hit ──────────────────────────────────────────────────────────────────


def test_span_hit_normalises_case_and_whitespace() -> None:
    assert span_hit("Gamma  DELTA\n", "gamma delta")
    assert span_hit("zeta\t\teta", "ZETA ETA")


def test_span_hit_without_normalisation_is_exact() -> None:
    assert not span_hit("Gamma DELTA", "gamma delta", normalise=False)
    assert span_hit("Gamma DELTA", "DELTA", normalise=False)


def test_span_hit_misses_an_absent_span() -> None:
    assert not span_hit("alpha beta", "delta")


@pytest.mark.parametrize("span", ["", "   \n\t"])
def test_span_hit_rejects_an_empty_span(span: str) -> None:
    """An empty span is a substring of everything — a broken label, not a hit."""
    with pytest.raises(ValueError, match="evidence_span"):
        span_hit("anything", span)


# ── Ranking metrics, hand-computed ────────────────────────────────────────────


@pytest.mark.parametrize(("k", "expected"), [(1, 0.0), (2, 0.5), (3, 0.5), (4, 1.0), (9, 1.0)])
def test_recall_at_k(k: int, expected: float) -> None:
    assert recall_at_k(RANKED, SPANS, k) == expected


@pytest.mark.parametrize(("k", "expected"), [(1, 0.0), (2, 0.5), (4, 0.5)])
def test_mrr_at_k(k: int, expected: float) -> None:
    assert mrr_at_k(RANKED, SPANS, k) == expected


def test_mrr_is_one_when_the_top_chunk_is_relevant() -> None:
    assert mrr_at_k(["delta"], SPANS, 1) == 1.0


def test_ndcg_at_k_hand_computed() -> None:
    # Gains at ranks 2 and 4; ideal puts the two spans at ranks 1 and 2.
    dcg = 1 / math.log2(3) + 1 / math.log2(5)
    idcg = 1 / math.log2(2) + 1 / math.log2(3)
    assert ndcg_at_k(RANKED, SPANS, 4) == pytest.approx(dcg / idcg)


def test_ndcg_is_one_for_an_ideal_ranking() -> None:
    assert ndcg_at_k(["zeta eta", "delta", "noise"], SPANS, 3) == pytest.approx(1.0)


def test_ndcg_credits_a_repeated_span_once() -> None:
    """Overlapping windows repeating one span must not push nDCG above 1."""
    assert ndcg_at_k(["x delta", "delta again"], ["delta"], 2) == pytest.approx(1.0)


def test_ndcg_is_zero_with_no_relevant_chunk() -> None:
    assert ndcg_at_k(["alpha", "beta"], SPANS, 2) == 0.0


@pytest.mark.parametrize(("k", "expected"), [(1, 0.0), (2, 0.5), (4, 0.5)])
def test_context_precision_at_k(k: int, expected: float) -> None:
    assert context_precision_at_k(RANKED, SPANS, k) == expected


def test_context_precision_divides_by_returned_chunks_not_k() -> None:
    assert context_precision_at_k(["delta"], SPANS, 5) == 1.0


def test_context_precision_of_an_empty_ranking_is_zero() -> None:
    assert context_precision_at_k([], SPANS, 5) == 0.0


def test_parse_recall_counts_surviving_spans() -> None:
    assert parse_recall("prefix Gamma Delta suffix", SPANS) == 0.5
    assert parse_recall("delta ... zeta eta", SPANS) == 1.0
    assert parse_recall("", SPANS) == 0.0


# ── Input validation ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("metric", RANKING_METRICS, ids=lambda m: m.__name__)
def test_ranking_metrics_reject_k_below_one(metric: RankingMetric) -> None:
    with pytest.raises(ValueError, match="k must be >= 1"):
        metric(RANKED, SPANS, 0)


@pytest.mark.parametrize("metric", RANKING_METRICS, ids=lambda m: m.__name__)
def test_ranking_metrics_reject_no_evidence(metric: RankingMetric) -> None:
    with pytest.raises(ValueError, match="evidence_spans"):
        metric(RANKED, [], 1)


def test_parse_recall_rejects_no_evidence() -> None:
    with pytest.raises(ValueError, match="evidence_spans"):
        parse_recall("text", [])


def test_bootstrap_rejects_unequal_lengths() -> None:
    with pytest.raises(ValueError, match="equal lengths"):
        cluster_bootstrap_delta([0.0, 1.0], [0.0], ["d1", "d1"])


def test_bootstrap_rejects_no_questions() -> None:
    with pytest.raises(ValueError, match="at least one question"):
        cluster_bootstrap_delta([], [], [])


@pytest.mark.parametrize("confidence", [0.0, 1.0, -0.5, 1.5])
def test_bootstrap_rejects_confidence_outside_open_unit_interval(confidence: float) -> None:
    with pytest.raises(ValueError, match="confidence"):
        cluster_bootstrap_delta([0.0], [1.0], ["d1"], confidence=confidence)


def test_bootstrap_rejects_zero_iterations() -> None:
    with pytest.raises(ValueError, match="iterations"):
        cluster_bootstrap_delta([0.0], [1.0], ["d1"], iterations=0)


def test_bootstrap_rejects_non_finite_scores() -> None:
    with pytest.raises(ValueError, match="finite"):
        cluster_bootstrap_delta([math.nan], [1.0], ["d1"])


def test_bootstrap_result_rejects_an_inverted_interval() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        BootstrapResult(delta=0.0, ci_low=0.1, ci_high=-0.1)


def test_bootstrap_result_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError, match="finite"):
        BootstrapResult(delta=math.inf, ci_low=0.0, ci_high=0.0)


# ── Cluster bootstrap ─────────────────────────────────────────────────────────


def test_identical_arms_give_zero_delta_and_a_ci_containing_zero() -> None:
    scores = [0.0, 0.5, 1.0, 0.25]
    result = cluster_bootstrap_delta(scores, scores, ["d1", "d1", "d2", "d3"])
    assert result.delta == 0.0
    assert result.ci_low <= 0.0 <= result.ci_high


def test_bootstrap_is_deterministic_under_a_fixed_seed() -> None:
    a = [0.0, 0.5, 1.0, 0.0, 1.0]
    b = [1.0, 0.5, 1.0, 0.5, 0.0]
    clusters = ["d1", "d1", "d2", "d3", "d3"]
    first = cluster_bootstrap_delta(a, b, clusters, iterations=500, seed=7)
    second = cluster_bootstrap_delta(a, b, clusters, iterations=500, seed=7)
    assert first == second


def test_bootstrap_ignores_input_order_of_clusters() -> None:
    """Same questions, permuted: the seed alone fixes the resample."""
    forward = cluster_bootstrap_delta([0.0, 0.0, 0.0], [1.0, 0.0, 0.5], ["d1", "d2", "d3"])
    reverse = cluster_bootstrap_delta([0.0, 0.0, 0.0], [0.5, 0.0, 1.0], ["d3", "d2", "d1"])
    assert forward == reverse


def test_single_cluster_collapses_the_interval_to_the_point_delta() -> None:
    # Diffs 1.0 and 0.0 in one document: every resample redraws that document.
    result = cluster_bootstrap_delta([0.0, 0.5], [1.0, 0.5], ["d1", "d1"], iterations=50)
    assert result == BootstrapResult(delta=0.5, ci_low=0.5, ci_high=0.5)


def test_bootstrap_resamples_whole_documents_not_questions() -> None:
    """d1 holds three questions with diff 1, d2 one with diff 0.

    Drawing two clusters gives a mean of 1 (d1,d1), 3/4 (mixed) or 0 (d2,d2,
    probability 1/4) — so a 99 % interval spans [0, 1]. Question-level
    resampling would make 0 vanishingly rare (1/4 ** 4) and pull the bound up.
    """
    result = cluster_bootstrap_delta(
        [0.0, 0.0, 0.0, 0.0],
        [1.0, 1.0, 1.0, 0.0],
        ["d1", "d1", "d1", "d2"],
        iterations=2_000,
        confidence=0.99,
    )
    assert result.delta == 0.75
    assert result.ci_low == 0.0
    assert result.ci_high == 1.0


# ── Decision rule (spec-0035 "Pre-registered decision rule") ──────────────────

_NO_REGRESSION = {"table": 0.0, "prose": 0.01}


def test_decide_adopts_a_ci_strictly_above_zero() -> None:
    result = BootstrapResult(delta=0.1, ci_low=0.02, ci_high=0.2)
    assert decide(result, stratum_deltas=_NO_REGRESSION) == DECISION_ADOPT


def test_decide_reports_a_ci_spanning_zero_as_inconclusive() -> None:
    result = BootstrapResult(delta=0.01, ci_low=-0.02, ci_high=0.05)
    assert decide(result, stratum_deltas=_NO_REGRESSION) == DECISION_INCONCLUSIVE


def test_decide_treats_a_lower_bound_of_exactly_zero_as_inconclusive() -> None:
    """Rule (a) is strict: ``ci_low > 0``."""
    result = BootstrapResult(delta=0.05, ci_low=0.0, ci_high=0.1)
    assert decide(result, stratum_deltas=_NO_REGRESSION) == DECISION_INCONCLUSIVE


def test_decide_rejects_a_ci_entirely_below_zero() -> None:
    result = BootstrapResult(delta=-0.1, ci_low=-0.2, ci_high=-0.01)
    assert decide(result, stratum_deltas=_NO_REGRESSION) == DECISION_REJECT


def test_a_stratum_regression_vetoes_an_otherwise_adopted_result() -> None:
    result = BootstrapResult(delta=0.1, ci_low=0.02, ci_high=0.2)
    strata = {"table": -(DEFAULT_MAX_STRATUM_REGRESSION + 0.01), "prose": 0.2}
    assert decide(result, stratum_deltas=strata) == DECISION_REJECT


def test_a_regression_of_exactly_the_tolerance_is_not_a_veto() -> None:
    """Rule (b) forbids regressing by *more than* five points."""
    result = BootstrapResult(delta=0.1, ci_low=0.02, ci_high=0.2)
    strata = {"table": -DEFAULT_MAX_STRATUM_REGRESSION}
    assert decide(result, stratum_deltas=strata) == DECISION_ADOPT


def test_decide_honours_a_raised_min_ci_low() -> None:
    result = BootstrapResult(delta=0.1, ci_low=0.02, ci_high=0.2)
    assert decide(result, stratum_deltas=_NO_REGRESSION, min_ci_low=0.05) == DECISION_INCONCLUSIVE


def test_decide_refuses_an_empty_stratum_breakdown() -> None:
    """Fail closed: a missing breakdown must not pass the veto vacuously."""
    result = BootstrapResult(delta=0.1, ci_low=0.02, ci_high=0.2)
    with pytest.raises(ValueError, match="stratum_deltas"):
        decide(result, stratum_deltas={})


def test_decide_rejects_a_negative_tolerance() -> None:
    result = BootstrapResult(delta=0.1, ci_low=0.02, ci_high=0.2)
    with pytest.raises(ValueError, match="max_stratum_regression"):
        decide(result, stratum_deltas=_NO_REGRESSION, max_stratum_regression=-0.01)


def test_decide_rejects_non_finite_stratum_deltas() -> None:
    result = BootstrapResult(delta=0.1, ci_low=0.02, ci_high=0.2)
    with pytest.raises(ValueError, match="finite"):
        decide(result, stratum_deltas={"table": math.nan})


# ── Properties ────────────────────────────────────────────────────────────────

_WORDS = st.text(alphabet="ab", min_size=1, max_size=3)
_CHUNKS = st.lists(st.text(alphabet="ab ", max_size=12), max_size=8)


@given(ranked=_CHUNKS, spans=st.lists(_WORDS, min_size=1, max_size=4), k=st.integers(1, 10))
def test_recall_is_bounded_and_monotonic_in_k(ranked: list[str], spans: list[str], k: int) -> None:
    here = recall_at_k(ranked, spans, k)
    assert 0.0 <= here <= 1.0
    assert recall_at_k(ranked, spans, k + 1) >= here
