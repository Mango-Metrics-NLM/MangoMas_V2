# Spec-0046: pass^k trials as the reliability metric of record

- **Status:** Implemented
- **Linked ADR:** _none — no boundary change_ (the `Target` / `Scorer` / `Sink` protocols are untouched; `EvalReport` and `GateResult` gain default-safe fields)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added — pass^k trials as the reliability metric of record (spec-0046 / reliability plan C1)`
- **Delivery plan:** [`docs/plans/20260916T214636Z-reliability-evidence-plan.md`](../docs/plans/20260916T214636Z-reliability-evidence-plan.md) milestone C1; Tier 2 item 1 of [`docs/plans/20261109T000000Z-measured-eval-and-governance-plan.md`](../docs/plans/20261109T000000Z-measured-eval-and-governance-plan.md)

## Problem

`EvalRunner` scores every dataset row exactly once, so `pass_rate` is a
capability measure: an agent that gets a row right nine times in ten passes
it. For an agent platform the question a gate should answer is *does it pass
every time* — Kapoor et al.'s pass^k, the probability that all `k` independent
attempts succeed, which at `k=8` turns a 90 % per-attempt agent into 43 %. The
reliability plan (C1) names pass^k the metric of record and assigns it to the
*runner*, not to a scorer: a scorer sees one prediction and cannot repeat the
dispatch. Nothing in `EvalReport` today distinguishes "passed once" from
"passed every time".

## Decision, dialectically

- **Thesis.** Add `MANGOMAS_EVAL__TRIALS=k`; keep one `EvalRowResult` per
  dataset row whose verdict is the conjunction of its `k` attempts; add
  `EvalReport.pass_pow_k`; gate it with `MIN_PASS_POW_K`.
- **Counter-argument.** Under that collapse `pass_rate` *becomes* pass^k, so a
  baseline recorded at `k=1` and a run at `k=5` are not comparable, and
  `MIN_PASS_POW_K` duplicates `MIN_PASS_RATE`. Keeping every attempt as its own
  row would preserve pass@1 — but `diff_reports`, `slice_stats`, the sinks and
  the baselines all key on `row_id`, and duplicate ids silently collapse to the
  last attempt.
- **Rebuttal (a third reading).** The non-comparability is the finding, not a
  defect: a row that was a pass at `k=1` and is flaky at `k=5` *should* appear
  in `regressed_rows`. What is needed is that the report says which metric it
  carries and keeps the per-attempt view beside it — `trials`, `pass_pow_k`
  and `trial_pass_rate` (passed attempts / all attempts, the pass@1 estimate).
  `MIN_PASS_POW_K` earns its separate name by being a *reliability* threshold:
  it fails closed when the run has `trials < 2`, which `MIN_PASS_RATE` cannot
  do, and which the cost gate's skip-when-unavailable rule (reviewed on PR
  #124 as a silently disabled budget) must not be copied for.
- **Decision.** Thesis with the rebuttal's three fields and the fail-closed
  rule; the "one row per attempt" layout is rejected.

## Requirements

- R1 `MANGOMAS_EVAL__TRIALS` (`DEFAULT_EVAL_TRIALS = 1`, validated `>= 1`),
  CLI `--trials` / `-k`. `EvalRunner(trials=...)` repeats each row `k` times,
  sequentially, each attempt with its own correlation id; `parallelism` still
  bounds concurrent rows.
- R2 `collapse_trials(attempts) -> EvalRowResult`: `passed` iff every attempt
  was a clean pass; `score` = mean over non-errored attempts (`0.0` if all
  errored); `error` = the first attempt's error when any attempt errored;
  `duration_ms` summed; `metadata.cost_usd` = mean over attempts that reported
  one; `metadata.trials = {count, passed, errored, scores, correlation_ids}`;
  prediction / expected / response metadata / correlation id from the first
  attempt. A single attempt is returned **unchanged**.
- R3 `EvalReport.trials` (default `1`), `pass_pow_k` (= `passed /
  dataset_size`; `0.0` for an empty dataset), `trial_pass_rate` (passed
  attempts / all attempts). Both floats default to `None` on the dataclass so
  a baseline written before them loads; the runner always sets them.
- R4 `MANGOMAS_EVAL__MIN_PASS_POW_K` (`[0, 1]`, default unset), CLI
  `--min-pass-pow-k`; engages the gate like every other threshold and is
  composed through `merge_gate_results`. `GateResult.min_pass_pow_k` /
  `actual_pass_pow_k` are additive.
- R5 Fail-closed: with `min_pass_pow_k` set and `report.trials < 2` the gate
  **fails** with `min_pass_pow_k requires trials >= 2`; a missing or
  non-finite `pass_pow_k` also fails. An empty dataset still passes with the
  existing informational note (nothing to gate).
- R6 Sinks: console prints `trials=k pass_pow_k=… trial_pass_rate=…` only when
  `k > 1`; `sqlite_results` adds `trials INTEGER` and `pass_pow_k REAL` through
  the existing additive-column migration; `json_file` / `webhook` carry the
  fields via `dataclasses.asdict`; `load_baseline` reads them with defaults.
- Must remain **additive & default-OFF**: `TRIALS=1` and `MIN_PASS_POW_K`
  unset reproduce today's rows, report and gate byte for byte.

## Scenarios (WHEN/THEN)

- WHEN `trials=3` and one dataset row's attempts score pass, fail, pass THEN
  the row is `passed=False` with `score ≈ 0.667`, `metadata.trials.passed == 2`,
  `report.pass_pow_k == 0.0` and `report.trial_pass_rate ≈ 0.667`
  (`test_pass_pow_k_falls_when_one_trial_of_k_fails`).
- WHEN `trials=3` and every attempt passes THEN the row passes, `pass_pow_k ==
  trial_pass_rate == 1.0`.
- WHEN `trials=1` THEN the row object is the attempt itself and the report
  equals a pre-spec report plus `trials=1`, `pass_pow_k == pass_rate`,
  `trial_pass_rate == pass_rate`.
- WHEN one of `k` attempts errors THEN the row carries that error (so
  `fail_on_error` fires) and `errored` counts it once.
- WHEN `min_pass_pow_k=0.5` and the run used `trials=1` THEN the gate fails
  with the trials reason, even at `pass_rate == 1.0`; AND WHEN `trials=2` and
  `pass_pow_k >= 0.5` THEN the gate passes (both directions).
- WHEN a `cost_budget` row reports a cost on each attempt THEN the collapsed
  row's `cost_usd` is their mean and `mean_cost_usd` still gates.
- WHEN the CLI is given `--trials 0` THEN exit 2.

## Config / env additions

| Env var (`MANGOMAS_*`) | Default | Purpose |
|------------------------|---------|---------|
| `MANGOMAS_EVAL__TRIALS` | `1` | Attempts per row; a row passes only when all pass (pass^k) |
| `MANGOMAS_EVAL__MIN_PASS_POW_K` | _(unset)_ | Reliability gate threshold on `pass_pow_k` `[0,1]`; fails closed when `TRIALS < 2` |

## Protocol / contract impact

- New/changed protocols: _none_.
- New error types: _none_ (`ValueError` at Settings parse / runner
  construction; CLI exit 2 for `--trials < 1` and an out-of-range threshold).
- Registry additions: _none_.

## Backwards-compatibility

- `TRIALS=1`: `collapse_trials` returns the single attempt object itself;
  `EvalReport` gains `trials=1`, `pass_pow_k`, `trial_pass_rate` (both equal
  to the pass rate) and nothing else changes. Existing baselines load with
  `trials=1` and `None` for the two floats; `diff_reports` ignores all three.
- `GateResult` gains two defaulted fields; `merge_gate_results` sources them
  from the threshold verdict like the cost pair.
- `sqlite_results` migrates existing databases in place (additive columns).

## Test plan

- Unit: `tests/eval/test_runner_trials.py` (collapse rule, pass^k, errors,
  cost mean, `k=1` identity, concurrency path, validation);
  `tests/eval/test_gate.py` (three `min_pass_pow_k` branches, merge carries
  the fields); `tests/eval/test_cli_eval.py` (`--trials`, `--min-pass-pow-k`
  exit 3, `--trials 0` exit 2, env precedence); `tests/eval/test_sinks.py`,
  `test_sqlite_results_sink.py`, `test_regression_gate.py` (baseline
  round-trip); `tests/test_config.py` (validators).
- Gates: both directions per the Scenarios; the fail-closed branch is the
  guard that cannot be vacuously green.
- Coverage: `eval` / `cli` / `config` stay at 100 %.

## Acceptance criteria

- [x] `make gate` green with `TRIALS` unset — no baseline, dataset or lane changed.
- [x] `test_pass_pow_k_falls_when_one_trial_of_k_fails` red before R2, green after.
- [x] A `k=1` run with `MIN_PASS_POW_K` set exits 3 with the trials reason.
- [x] `docs/eval/harness.md` "Trials and pass^k" section; env rows in
  `AGENTS.md` / `.env.example`; CHANGELOG entry; reliability plan C1 ticked.
