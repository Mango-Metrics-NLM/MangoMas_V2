# ADR-0005: Eval regression / baseline gating

## Status

Accepted

## Context

The threshold gate (ADR-0003) judges a run against absolute thresholds, but CI
often needs a *relative* judgement: "did this change make the eval worse than the
last known-good run?" We already persist a full `EvalReport` as a `json_file`
sink artifact, and the `echo` target (ADR-0004) gives deterministic baselines for
testing. We want to diff a run against a stored baseline and fail CI on
regression, reusing the existing gate plumbing (exit code 3, sink rendering).

## Decision

Add a pure `diff_reports(baseline, current) -> ReportDiff` (per-metric deltas +
per-row regressed/new/dropped partition) and `evaluate_regression_gate(diff,
*, max_mean_score_drop, max_pass_rate_drop, allow_new_failures) -> GateResult`.
Baselines are loaded from the `json_file` artifact via `load_baseline` (the
canonical format — human-diffable, already produced). The CLI gains `--baseline`
+ tolerance flags; threshold and regression verdicts are combined with
`merge_gate_results` into the single `GateResult` handed to sinks, so the
existing exit-3 path covers either.

## Consequences

### Positive

- Regression gating reuses `GateResult` (sinks unchanged) and the exit-3
  contract; `json_file` stays the single baseline format (no second store).
- `diff_reports` is pure (only telemetry/log side effects) → Hypothesis-friendly.
- A missing baseline is exit 2 (config) via an up-front existence check; a
  malformed one surfaces during the run as exit 1.

### Negative / Trade-offs

- Row-level regression detection keys on `row_id` stability across runs; rows
  whose ids change appear as dropped + new rather than regressed.

### Neutral

- Deltas are `current - baseline` (a "drop" is `baseline - current`); the gate
  compares the drop against the configured tolerance.

## Alternatives Considered

- **SQLite as the baseline store** — rejected: `json_file` is human-diffable and
  already the artifact contract; the `sqlite_results` sink (ADR/Capability D)
  remains a queryable secondary store, not the baseline source.
- **A separate regression exit code** — rejected: reusing exit 3 keeps CI wiring
  simple; the merged `GateResult` reasons distinguish the cause.

## References

- Code: `src/mangomas/eval/baseline.py` (`load_baseline`, `diff_reports`,
  `ReportDiff`), `eval/gate.py` (`evaluate_regression_gate`,
  `merge_gate_results`), `cli/main.py` (`_evaluate_run_gates`,
  `_resolve_regression`).
- Related ADRs: ADR-0003 (eval harness), ADR-0004 (target/source indirection).
