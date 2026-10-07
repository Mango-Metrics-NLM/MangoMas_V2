"""Pure retrieval/parse metrics and the pre-registered decision rule (spec-0035 R12).

Everything here is stdlib-only, deterministic and free of I/O, so it is unit
tested in the **default** suite (``test_metrics.py``) while the live bake-off
that feeds it stays behind ``RUN_DOCLING=1``.

Relevance is label-based: a question is labelled with one or more *evidence
spans* (verbatim snippets of the source document), and a chunk is relevant to
the question when it contains a span (:func:`span_hit`). No metric here reads
model output, embedding values or timings.

Every tunable is a keyword parameter whose default is a named module constant,
so the harness can only change a threshold by saying so at the call site.
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

# ── Tunables (defaults fixed before any measurement; spec-0035) ───────────────
# Bootstrap resamples. 10k keeps the Monte-Carlo error of a 95 % percentile
# bound well below the 5-point stratum tolerance.
DEFAULT_BOOTSTRAP_ITERATIONS: int = 10_000
# Fixed seed so a recorded bake-off report is reproducible bit-for-bit.
DEFAULT_BOOTSTRAP_SEED: int = 0
# Two-sided confidence level of the percentile interval ("the 95 % CI").
DEFAULT_CONFIDENCE: float = 0.95
# Rule (a): the CI lower bound of Δrecall@5 must be strictly above this.
DEFAULT_MIN_CI_LOW: float = 0.0
# Rule (b): no stratum may regress by more than 5 points. Metrics are fractions
# in [0, 1], so 5 points is 0.05.
DEFAULT_MAX_STRATUM_REGRESSION: float = 0.05

Decision = Literal["adopt", "reject", "inconclusive"]
DECISION_ADOPT: Decision = "adopt"
DECISION_REJECT: Decision = "reject"
DECISION_INCONCLUSIVE: Decision = "inconclusive"

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class BootstrapResult:
    """Paired candidate-minus-baseline delta with its percentile CI."""

    delta: float
    ci_low: float
    ci_high: float

    def __post_init__(self) -> None:
        values = (self.delta, self.ci_low, self.ci_high)
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"bootstrap result must be finite, got {values!r}")
        if self.ci_low > self.ci_high:
            raise ValueError(f"ci_low {self.ci_low!r} exceeds ci_high {self.ci_high!r}")


# ── Input validation ──────────────────────────────────────────────────────────


def _require_k(k: int) -> None:
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k!r}")


def _require_spans(evidence_spans: Sequence[str]) -> None:
    if not evidence_spans:
        raise ValueError("evidence_spans must name at least one span")


def _require_confidence(confidence: float) -> None:
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1), got {confidence!r}")


def _normalise(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip().casefold()


# ── Span matching ─────────────────────────────────────────────────────────────


def span_hit(chunk_text: str, evidence_span: str, *, normalise: bool = True) -> bool:
    """Whether *chunk_text* contains *evidence_span*.

    With ``normalise`` (the default) both sides are case-folded and every run of
    whitespace collapses to one space, so a chunker that re-wraps lines or a
    parser that changes case does not lose a hit. An empty span is rejected: it
    is a substring of everything and would make a broken label score as a hit.
    """
    span = _normalise(evidence_span) if normalise else evidence_span
    if not span:
        raise ValueError("evidence_span must be non-empty")
    text = _normalise(chunk_text) if normalise else chunk_text
    return span in text


def _chunk_is_relevant(chunk: str, evidence_spans: Sequence[str]) -> bool:
    return any(span_hit(chunk, span) for span in evidence_spans)


# ── Ranking metrics ───────────────────────────────────────────────────────────


def recall_at_k(ranked_chunks: Sequence[str], evidence_spans: Sequence[str], k: int) -> float:
    """Fraction of *evidence_spans* contained in at least one of the top-*k* chunks."""
    _require_k(k)
    _require_spans(evidence_spans)
    top = ranked_chunks[:k]
    found = sum(1 for span in evidence_spans if any(span_hit(chunk, span) for chunk in top))
    return found / len(evidence_spans)


def mrr_at_k(ranked_chunks: Sequence[str], evidence_spans: Sequence[str], k: int) -> float:
    """Reciprocal rank of the first top-*k* chunk containing any span; 0 if none."""
    _require_k(k)
    _require_spans(evidence_spans)
    for rank, chunk in enumerate(ranked_chunks[:k], start=1):
        if _chunk_is_relevant(chunk, evidence_spans):
            return 1.0 / rank
    return 0.0


def _discount(rank: int) -> float:
    return 1.0 / math.log2(rank + 1)


def ndcg_at_k(ranked_chunks: Sequence[str], evidence_spans: Sequence[str], k: int) -> float:
    """nDCG@k with span-level gains, each evidence span credited once.

    A chunk's gain is the number of evidence spans it contains that no
    higher-ranked chunk already contained. Gains and the ideal use the same
    unit (spans): the ideal DCG places every span at rank 1, the best any
    ranking can do, so the score stays in ``[0, 1]`` and moving evidence to a
    higher rank never lowers it. (A per-chunk binary gain normalised against
    one span per rank did the opposite: a single chunk holding all the
    evidence at rank 1 scored *below* the same chunks in reverse order, biasing
    the bake-off against chunkers that keep evidence together.)
    """
    _require_k(k)
    _require_spans(evidence_spans)
    unseen = list(evidence_spans)
    dcg = 0.0
    for rank, chunk in enumerate(ranked_chunks[:k], start=1):
        hits = [span for span in unseen if span_hit(chunk, span)]
        if hits:
            dcg += len(hits) * _discount(rank)
            unseen = [span for span in unseen if span not in hits]
    ideal = len(evidence_spans) * _discount(1)
    return dcg / ideal


def context_precision_at_k(
    ranked_chunks: Sequence[str], evidence_spans: Sequence[str], k: int
) -> float:
    """Fraction of the returned top-*k* chunks that contain any evidence span.

    The denominator is the number of chunks actually returned (at most *k*),
    so a retriever is not penalised for a corpus smaller than *k*; returning
    nothing scores 0.
    """
    _require_k(k)
    _require_spans(evidence_spans)
    top = ranked_chunks[:k]
    if not top:
        return 0.0
    relevant = sum(1 for chunk in top if _chunk_is_relevant(chunk, evidence_spans))
    return relevant / len(top)


def parse_recall(parsed_text: str, evidence_spans: Sequence[str]) -> float:
    """Fraction of *evidence_spans* that survive parsing into *parsed_text*.

    Separates parser loss from retrieval loss: a span the parser dropped can
    never be retrieved, whatever the chunker does.
    """
    _require_spans(evidence_spans)
    found = sum(1 for span in evidence_spans if span_hit(parsed_text, span))
    return found / len(evidence_spans)


# ── Document-cluster bootstrap ────────────────────────────────────────────────


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile of an already-sorted, non-empty sequence."""
    position = q * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    fraction = position - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * fraction


def cluster_bootstrap_delta(
    per_question_a: Sequence[float],
    per_question_b: Sequence[float],
    cluster_ids: Sequence[str],
    *,
    iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    confidence: float = DEFAULT_CONFIDENCE,
) -> BootstrapResult:
    """Paired ``mean(b - a)`` with a document-cluster percentile bootstrap CI.

    Arm *a* is the baseline and arm *b* the candidate, so a positive delta
    favours the candidate. Questions about the same document are correlated,
    so resampling questions independently would understate the variance; this
    resamples whole clusters (documents) with replacement and keeps each
    question's two arms paired.
    """
    if not (len(per_question_a) == len(per_question_b) == len(cluster_ids)):
        raise ValueError(
            "per_question_a, per_question_b and cluster_ids must have equal lengths, got "
            f"{len(per_question_a)}, {len(per_question_b)}, {len(cluster_ids)}"
        )
    if not cluster_ids:
        raise ValueError("at least one question is required")
    if iterations < 1:
        raise ValueError(f"iterations must be >= 1, got {iterations!r}")
    _require_confidence(confidence)
    diffs = [b - a for a, b in zip(per_question_a, per_question_b, strict=True)]
    if not all(math.isfinite(diff) for diff in diffs):
        raise ValueError("per-question scores must be finite")

    by_cluster: dict[str, list[float]] = {}
    for cluster, diff in zip(cluster_ids, diffs, strict=True):
        by_cluster.setdefault(cluster, []).append(diff)
    # Sorted so the resample depends on the seed only, never on input order.
    clusters = [by_cluster[key] for key in sorted(by_cluster)]

    rng = random.Random(seed)  # noqa: S311 -- statistical resampling, not security
    stats: list[float] = []
    for _ in range(iterations):
        drawn = rng.choices(clusters, k=len(clusters))
        total = sum(sum(cluster) for cluster in drawn)
        count = sum(len(cluster) for cluster in drawn)
        stats.append(total / count)
    stats.sort()

    alpha = 1.0 - confidence
    return BootstrapResult(
        delta=sum(diffs) / len(diffs),
        ci_low=_quantile(stats, alpha / 2),
        ci_high=_quantile(stats, 1.0 - alpha / 2),
    )


# ── Pre-registered decision rule ──────────────────────────────────────────────


def decide(
    result: BootstrapResult,
    *,
    stratum_deltas: Mapping[str, float],
    min_ci_low: float = DEFAULT_MIN_CI_LOW,
    max_stratum_regression: float = DEFAULT_MAX_STRATUM_REGRESSION,
) -> Decision:
    """Apply spec-0035's pre-registered rule to a candidate-minus-baseline result.

    * ``reject`` — any stratum regresses by more than *max_stratum_regression*
      (a veto that overrides a positive CI), or the whole CI is below zero.
    * ``adopt`` — ``ci_low > min_ci_low`` and no stratum veto.
    * ``inconclusive`` — otherwise, i.e. the CI spans zero (or does not clear
      *min_ci_low*). Never reported as a negative result.

    Rule (c) of the spec — parse p95 latency and cost within the recorded
    ceilings — is a property of the run, not of this result, and is checked by
    the harness alongside this function.

    *stratum_deltas* is required and must be non-empty: an omitted stratum
    breakdown would otherwise pass the veto vacuously.
    """
    if not stratum_deltas:
        raise ValueError("stratum_deltas must name at least one stratum")
    # Every comparison below is False for NaN, and +inf disables the veto, so a
    # malformed threshold or result would otherwise turn into "adopt".
    if not (math.isfinite(min_ci_low) and math.isfinite(max_stratum_regression)):
        raise ValueError("decision thresholds must be finite")
    if max_stratum_regression < 0:
        raise ValueError(f"max_stratum_regression must be >= 0, got {max_stratum_regression!r}")
    if not all(math.isfinite(delta) for delta in stratum_deltas.values()):
        raise ValueError("stratum deltas must be finite")
    if any(delta < -max_stratum_regression for delta in stratum_deltas.values()):
        return DECISION_REJECT
    if result.ci_high < 0:
        return DECISION_REJECT
    if result.ci_low > min_ci_low:
        return DECISION_ADOPT
    return DECISION_INCONCLUSIVE
