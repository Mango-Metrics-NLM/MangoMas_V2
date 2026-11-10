# Spec-0047: judge calibration record — `llm_judge` reported, not gated, until calibrated

- **Status:** Implemented (record + demotion; the calibration run and the judge prompt work are follow-ups)
- **Linked ADR:** _none — no boundary change_ (`Scorer` is untouched; `JudgeAware` is an additive capability protocol in `eval/`, `EvalReport` / `GateResult` gain default-safe fields)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added — judge calibration record: llm_judge reported, not gated, until calibrated (spec-0047 / reliability plan C2)`
- **Delivery plan:** [`docs/plans/20260916T214636Z-reliability-evidence-plan.md`](../docs/plans/20260916T214636Z-reliability-evidence-plan.md) milestone C2; Tier 2 item 2 of [`docs/plans/20261109T000000Z-measured-eval-and-governance-plan.md`](../docs/plans/20261109T000000Z-measured-eval-and-governance-plan.md)

## Problem

`llm_judge` (`src/mangomas/eval/scorers/llm_judge.py`) grades through
`ScorerContext.llm`, which the runner sets to the orchestrator's shared client —
the system under test's own model. Self-preference bias is therefore structural,
and until now the scorer's only acknowledgement was a WARNING (spec-0045 R7). Its
scores could still decide a gate: `--min-mean-score 0.8` over a judge run exits 3
or 0 on numbers nobody has checked against a person. ADR-0041 and AGENTS.md both
say "reported, not to be gated on until calibrated"; nothing mechanical made it so.

## Decision, dialectically

- **Thesis.** Load a record in the CLI, decide "calibrated?" once, pass a boolean
  to the gate; when false, judge-derived thresholds become informational.
- **Counter-argument.** A boolean hides *why* (stale, wrong model, too few rows)
  and loses the self-preference distinction the plan insists on; and a gate that
  exits 0 with a note on an uncalibrated judge is the "vacuous green" spec-0046
  refused for `MIN_PASS_POW_K`. Fail closed instead.
- **Rebuttal (a third reading).** The plan's posture — "exactly as `cost_budget`
  behaves" — is deliberate: the judge is *demoted*, not forbidden. A team must be
  able to keep running it to collect the scores a calibration needs, and CI must
  not go red for lack of a record. What prevents vacuity is honesty in the
  artefact: the report carries a structured `JudgeStatus`, every sink prints it,
  the gate's note names the reason and the self-preference claim, and
  `GateResult.judge_excluded` is machine-readable so a downstream consumer can
  refuse a green that rests on an uncalibrated judge. Fail-closed stays for a
  *reliability* claim (`MIN_PASS_POW_K`, where a single trial measures nothing);
  for a *quality* claim by an uncalibrated judge the honest output is "no claim",
  said out loud. A future opt-in (`JUDGE_REQUIRE_CALIBRATION`) is where the
  stricter stance belongs.
- **Decision.** Structured status on the report, stamped by the runner from a
  `JudgeAware` scorer; score thresholds (and judge-derived regression deltas)
  skipped with one informational note; `fail_on_error` and the cost threshold
  still apply; notes are printed on a passing gate (they were invisible before).

## Requirements

- R1 `eval/judge_calibration.py`: `JudgeCalibrationRecord` (pydantic,
  `extra="forbid"`: `judge_model`, `measured_at` date, `sample_size >= 1`,
  `cohens_kappa` / `spearman_rho` in `[-1, 1]` with at least one present,
  `self_preference ∈ {not_measured, measured_unbiased, measured_biased}`,
  `notes`); `load_calibration_record(path)` → `ConfigError` (exit 2) on a
  missing or malformed file; pure `assess_calibration(record, judge_model,
  policy, now)` → frozen `JudgeStatus` with the first failing rule as `reason`:
  model unknown → model mismatch (exact after `strip()`) → future date → older
  than `max_age_days` → `sample_size` below floor → `measured_biased` → neither
  κ nor ρ at its floor. `not_measured` never blocks.
- R2 Settings: `judge_calibration_path` (None), `judge_calibration_max_age_days`
  (180, `>= 1`), `judge_min_kappa` (0.8) / `judge_min_rho` (0.85) in `[0, 1]`,
  `judge_min_sample_size` (200, `>= 1`); `DEFAULT_EVAL_JUDGE_*` constants.
- R3 `JudgeAware` protocol (`eval/protocol.py`); `LLMJudgeScorer.judge_status`
  is **never None** — a judge built without a status is uncalibrated with reason
  `no calibration record`; the factory options `calibration_path`,
  `judge_model`, `min_sample_size`, `min_kappa`, `min_rho`, `max_age_days`
  build the status at construction. `CompositeScorer.judge_status` is the first
  judge child's status relabelled `… (child of composite)`.
- R4 The CLI seeds those options from `MANGOMAS_EVAL__JUDGE_*` and
  `MANGOMAS_LLM__MODEL` (`_seed_judge_options`; explicit options win; the judge
  always grades through the shared client, so the shared model id is the key
  even when the target runs a profile or `MODEL_OVERRIDE`).
- R5 `EvalReport.judge: JudgeStatus | None` stamped by the runner (both
  constructions); baselines load it with a field-filtered `_judge_from_dict`.
- R6 `evaluate_gate`: with an uncalibrated judge **and** any of
  `min_mean_score` / `min_pass_rate` / `min_pass_pow_k` configured, the score
  checks are replaced by one informational note and `judge_excluded=True`;
  without a configured score threshold the note exists but nothing is "not
  applied"; `fail_on_error` and the cost ladder run regardless.
  `GateResult.judge_note` is set in **both** states so the self-preference
  claim is never dropped. `evaluate_regression_gate(..., judge=)`: an
  uncalibrated judge collapses regression reasons into one note and passes.
  `merge_gate_results` ORs `judge_excluded` and carries the threshold note.
- R7 Sinks: console prints `judge=<summary>` whenever the report has one, and
  prints informational notes on a **passing** verdict (`gate=PASS (note: …)`);
  `sqlite_results` adds `judge_json`; JSON / webhook carry `judge` via `asdict`.
- Must remain **additive & default-OFF**: a deterministic scorer's report has
  `judge=None` and every verdict is byte-identical to before; a judge run
  with no thresholds configured changes only by the new `judge=` line.

## Scenarios (WHEN/THEN)

- WHEN a judge report has `judge.calibrated=False` and `--min-mean-score 0.9` with
  `mean_score 0.4` THEN the gate passes with exactly one informational reason
  naming the self-preference claim and `judge_excluded=True`
  (`test_uncalibrated_judge_scores_are_reported_but_not_gated`); AND WHEN the
  status is calibrated THEN the same report fails on `mean_score`.
- WHEN no score threshold is configured THEN an uncalibrated judge is not
  "excluded" (`judge_excluded=False`, `reasons=[]`), though `judge_note` exists.
- WHEN `fail_on_error` / `max_mean_cost_usd` would fail THEN they still fail under
  an uncalibrated judge.
- WHEN a regression diff would fail and the judge is uncalibrated THEN the
  regression verdict passes with one note; WHEN calibrated THEN it fails.
- WHEN the record names another model, is older than the limit, has too few
  rows, is `measured_biased`, or clears neither floor THEN `reason` names that
  rule; WHEN κ alone or ρ alone clears its floor THEN calibrated.
- WHEN `JUDGE_CALIBRATION_PATH` is set to a missing or malformed file THEN exit 2.
- WHEN `-s contains -s llm_judge` THEN the composite is demoted and the console
  names `llm_judge (child of composite)`.

## Config / env additions

| Env var (`MANGOMAS_*`) | Default | Purpose |
|------------------------|---------|---------|
| `MANGOMAS_EVAL__JUDGE_CALIBRATION_PATH` | _(unset)_ | JSON calibration record for `llm_judge` |
| `MANGOMAS_EVAL__JUDGE_CALIBRATION_MAX_AGE_DAYS` | `180` | Staleness limit |
| `MANGOMAS_EVAL__JUDGE_MIN_KAPPA` | `0.8` | Cohen's κ floor (κ or ρ suffices) |
| `MANGOMAS_EVAL__JUDGE_MIN_RHO` | `0.85` | Spearman ρ floor |
| `MANGOMAS_EVAL__JUDGE_MIN_SAMPLE_SIZE` | `200` | Minimum labelled rows |

## Protocol / contract impact

- New/changed protocols: additive `JudgeAware` in `eval/protocol.py` (eval-local capability; `Scorer` unchanged).
- New error types: _none_ (`ConfigError` for a bad record / option).
- Registry additions: _none_.

## Backwards-compatibility

- Deterministic scorers: `EvalReport.judge=None`, `GateResult.judge_excluded=False`,
  `judge_note=None`; verdicts unchanged. Existing baselines load with `judge=None`.
- Judge runs: previously a bare WARNING and gated thresholds; now the same WARNING
  carries the status and thresholds are demoted until a record exists. This is the
  intended behaviour change and is the whole point of C2.
- Console: a passing gate with informational notes now prints them
  (`gate=PASS (note: …)`); a clean pass still prints `gate=PASS`.

## Test plan

- `tests/eval/test_judge_calibration.py` (every rule both ways, boundaries, clock
  seam, schema, loader errors, JSON safety, child relabel);
  `tests/eval/test_gate.py` (named test, calibrated case, no-threshold case,
  errors/cost still fail, pass^k exclusion, regression exclusion, merge);
  `tests/eval/test_llm_judge.py` (bare judge uncalibrated, factory options, bad
  options, warning carries status); `test_composite.py`; `test_runner_trials.py`
  (stamping + baseline round trip); `test_cli_eval.py` (uncalibrated → exit 0
  with note, calibrated → exit 3, other model, malformed record → exit 2,
  composite child, `_seed_judge_options`); `test_sinks.py` /
  `test_sqlite_results_sink.py`.
- Gates: both directions per the Scenarios; mutation check — make `_judge_verdict`
  never exclude → the named test goes red.
- Coverage: `eval` / `cli` / `config` stay at 100 %.

## Acceptance criteria

- [x] A judge run with `--min-mean-score` exits 0 with the note and `judge_excluded=true` in the JSON report; the same run with a valid record for `MANGOMAS_LLM__MODEL` exits 3.
- [x] "self-preference: not_measured" and "self-preference: measured_unbiased" are both printed verbatim and never read the same.
- [x] `make gate` green with every new env var unset; no baseline, dataset or lane changed.
- [x] Docs: harness.md "Judge calibration" section with the record schema and how to produce one; env rows in AGENTS.md / .env.example; CHANGELOG; C2 ticked in both plans.
