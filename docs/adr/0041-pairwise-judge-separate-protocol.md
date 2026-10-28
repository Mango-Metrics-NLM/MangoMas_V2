# ADR-0041: Pairwise judgement is a separate protocol, not a `Scorer`

## Status

Proposed

## Context

Spec-0043 adds head-to-head comparison of two eval targets by an LLM judge
that sees both answers. The existing `Scorer` protocol is
`score(prediction, expected, *, context) -> ScoreResult`: one answer, one
reference, one scalar. Three options were on the table: (a) overload
`Scorer` so `expected` may carry the second prediction; (b) encode the pair
into `prediction` as JSON and add a scorer that unpacks it; (c) a new
protocol. Position bias — the judge preferring whichever answer is listed
first — is a measured failure of pairwise LLM judging, so the comparison
must call the judge twice with the order swapped, which no single `score`
call can express.

## Decision

Pairwise judgement is its own `@runtime_checkable` protocol,
`PairwiseJudge`, with its own registry (`pairwise_judge_registry`) and its
own result type (`PairwiseVerdict`). `Scorer` is unchanged. The order swap
is part of the built-in judge's contract, not an option.

## Consequences

### Positive

- `Scorer` keeps a single meaning; the seven existing scorers and every
  plugin discovered through `mangomas.eval.scorers` are untouched.
- The registry's protocol check refuses a `Scorer` registered as a judge
  and vice versa, so the two cannot be confused by name.
- The two-call swap is structural: a judge that cannot be called twice
  cannot be registered, so position bias is always measured.

### Negative / Trade-offs

- A second runner entry point (`run_paired`) and a sibling report type
  (`PairedReport`) rather than one polymorphic `run`; some duplication in
  the CLI and `_serialize`.
- Two LLM calls per row, by contract. Judge cost is accounted separately
  so it cannot be hidden, but it is real.

### Neutral

- `EvalReport` and `baseline.load_baseline` round-trip only single-target
  reports; paired results have their own payload document.

## Alternatives Considered

- **Alt 1:** overload `Scorer.expected` with the B prediction — rejected
  because every existing scorer would silently "work" on a pair while
  meaning something else, and `json_keys` / `exact_match` would produce
  numbers that look like comparisons and are not.
- **Alt 2:** JSON-pack the pair into `prediction` — rejected because the
  pairing contract would live in a string format no type checks, and the
  swap would have to be reimplemented by every judge author.
- **Alt 3:** make the order swap an option defaulting to on — rejected
  because the single-call mode has no legitimate use and an option is a
  place for it to be turned off "for cost".

## References

- Code: `src/mangomas/eval/protocol.py` (`Scorer`, lines 59–69 at the time
  of writing), `src/mangomas/eval/scorers/llm_judge.py`
- Specs: spec-0042, spec-0043, spec-0040 (judge calibration, reserved)
- Related ADRs: ADR-0003 (eval harness), ADR-0004 (targets and sources),
  ADR-0030 (plugin override refusal)
