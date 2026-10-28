# Spec-0042: Paired evaluation statistics

- **Status:** Draft
- **Linked ADR:** _none — no boundary change_ (pure functions inside `eval/`; one additive CLI subcommand)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added`
- **Delivery plan:** `docs/plans/20261027T220000Z-civ6-method-port-plan.md` (PR 1)

## Problem

Every scorer in `mangomas.eval` grades one answer on its own, and
`baseline.diff_reports` compares two reports by absolute `mean_score` /
`pass_rate` deltas with no uncertainty. The questions the harness is now
asked — planner-only vs planner+reviewer, Gemma vs Gemini, a prompt change
against its predecessor — are *paired* questions: same rows, two
configurations, which one wins and by how much, with an interval the reader
can trust. Civ6-AlphaZero solved the identical problem in
`alpha_zero/evaluation/scoring.py` after shipping a promotion gate whose
statistic made promotion arithmetically impossible above a 45 % draw rate
and whose interval was wrong by exactly `draw_rate/4` in variance. This spec
ports the **method**, rewritten against `EvalReport`, with the clustered
bootstrap the Civ6 code lacks.

## Requirements

- `src/mangomas/eval/paired.py` exposes pure functions only (no I/O, no
  LLM, no numpy — `eval/` stays dependency-free):
  - `pair_reports(a: EvalReport, b: EvalReport, *, draw_epsilon: float) -> PairedRows`
    — rows matched by `row_id`; a pair is `a` / `b` / `draw` by comparing
    per-row `score` within `draw_epsilon`; rows errored on **either** side
    are excluded and counted; `dropped_rows` / `new_rows` are reported as
    `diff_reports` does.
  - `score_rate(wins, draws, losses)`, `score_variance(...)`
    (ternary: outcomes in `{0, 0.5, 1}`), `score_interval(...)`,
    `effective_n(...)`, `minimum_detectable_effect(n, draw_rate, alpha, power)`.
  - `bootstrap_interval(rows, *, group_key, resamples, seed)` resamples
    **groups** read from `EvalRowResult.metadata[group_key]`; rows without
    the key are singleton groups and the count is surfaced as
    `ungrouped_rows`.
  - `summarize_paired(...) -> PairedSummary` (frozen dataclass: counts,
    score rate, draw rate, decisive win rate, analytic CI, bootstrap CI,
    `effective_n`, `ungrouped_rows`, `group_count`, `schema_version`).
  - `evaluate_paired_gate(summary, *, threshold, confidence) -> GateResult`
    — verdict is **lower confidence bound ≥ threshold** (Civ6
    `score_rate_lower_bound`), never the point estimate; combinable via
    `merge_gate_results`.
- `mangomas eval compare --a <report.json> --b <report.json>
  [--group-key task_family] [--threshold F] [--confidence F] [--gate]
  [--out paired.json]` — reads both reports through `baseline.load_baseline`
  (the one payload shape), writes `paired_payload(summary)` through the
  same `_serialize` module, exits **3** on gate failure *after* the payload
  is written, **2** when the reports share zero scorable rows.
- Draws are scored 0.5 everywhere. A statistic that scores a draw as a loss
  MUST NOT exist in this module (the Civ6 defect, recorded so it cannot be
  re-introduced "for simplicity").
- Elo / TrueSkill are **out of scope** below 5 concurrently compared
  configurations; `DEFAULT_PAIRED_MAX_CONFIGS_FOR_PAIRWISE = 4` is recorded
  as the re-open threshold, not enforced.
- Must remain **additive & default-OFF**: no change to `mangomas eval`
  (single-report path), no new sink, no settings read on the existing path.

## Scenarios (WHEN/THEN)

- WHEN the counts are 142 / 588 / 70 THEN `score_variance` equals the
  ternary value AND the binomial-on-fractional-successes variance exceeds
  it by `draw_rate / 4` (test asserts the difference, not just inequality).
- WHEN every row is a draw THEN `score_rate == 0.5` AND `decisive_win_rate`
  is `None` AND the gate at threshold `0.5` does not fire (a draw is not a
  regression) AND at threshold `0.55` it fires.
- WHEN B scores lower than A on every row THEN `evaluate_paired_gate`
  fails AND WHEN B scores higher on every row THEN it passes AND WHEN A and
  B are identical THEN the test first asserts the inputs are a tie and the
  summary reports `decisive_games == 0` (never a winner by index order).
- WHEN every row of a group shares its outcome THEN the group bootstrap
  interval is wider than a row bootstrap over the same rows AND WHEN rows
  are independent THEN the two intervals agree within the resampling
  tolerance (both directions: the grouping must do something, and must not
  do something when there is nothing to do).
- WHEN `bootstrap_interval` runs twice with the same seed THEN the results
  are byte-identical AND WHEN the seed differs THEN they differ (the Civ6
  "diff two seeded runs" method — seeding is proved by diffing, not by
  reading call sites).
- WHEN the reports share no `row_id` THEN `compare` exits 2 AND no payload
  is written AND WHEN they share one row THEN a payload is written with
  `effective_n == 1` and the gate reports insufficient evidence rather than
  passing.
- WHEN a row errored in A only THEN it is excluded from pairs and counted
  in `errored_a` AND WHEN it errored in neither THEN it is paired.

## Config / env additions

| Env var (`MANGOMAS_*`) | Default | Purpose |
|------------------------|---------|---------|
| `MANGOMAS_EVAL__PAIRED_OPTIONS` | `{}` | Per-run overrides keyed `draw_epsilon`, `group_key`, `resamples`, `seed`, `threshold`, `confidence` |

Module constants in `config/evaluation.py`, re-exported (`X as X`) into
`tests.constants.eval`: `DEFAULT_PAIRED_DRAW_EPSILON = 1e-9`,
`DEFAULT_PAIRED_GROUP_KEY = "task_family"`,
`DEFAULT_PAIRED_BOOTSTRAP_RESAMPLES = 2000`,
`DEFAULT_PAIRED_BOOTSTRAP_SEED = 0`, `DEFAULT_PAIRED_THRESHOLD = 0.5`,
`DEFAULT_PAIRED_CONFIDENCE = 0.95`, `DEFAULT_PAIRED_MIN_ROWS = 30` (below
it the gate verdict is `insufficient_evidence`, which is a failure when
`--gate` is set — never a pass), `DEFAULT_PAIRED_MAX_CONFIGS_FOR_PAIRWISE = 4`.

## Protocol / contract impact

- New/changed protocols: _none_. `Scorer`, `Sink`, `Target` untouched.
- New error types: _none_ — zero overlap is `ConfigError` (exit 2).
- Registry additions: _none_.
- Payload: `paired_payload` adds a top-level `"paired"` document with its
  own `schema_version: 1`; `report_payload` is unchanged.

## Backwards-compatibility

- `mangomas eval` without `compare` is byte-identical: no import of
  `eval/paired.py` on that path (test proves it via `sys.modules`).
- `EvalRowResult.metadata[group_key]` is read, never required; a dataset
  without the key still compares, with `ungrouped_rows == dataset_size`.

## Test plan

- Unit: `tests/eval/test_paired.py` (statistics, both-direction gate,
  bootstrap grouping, determinism), `tests/eval/test_cli_compare.py`
  (exit codes, payload written before exit 3, zero-overlap → 2). Fixtures
  built from the `echo` target with two different `text` options so the
  suite needs no LLM; the 142/588/70 triple and the expected variance live
  in `tests/constants/eval.py`.
- Hypothesis: `score_rate ∈ [0,1]`, `score_variance ≥ 0`, interval bounds
  ordered, `effective_n ≤ total`, pairing is symmetric under swapping A and
  B (winner flips, draws invariant).
- Gates: every scenario above is written both directions. Mutation proofs
  named in the PR body: (1) change draws to score 0 → ternary test red;
  (2) resample rows instead of groups → correlated-group test red;
  (3) gate on point estimate instead of lower bound → the "narrow win,
  wide interval" fixture goes green when it must be red.
- Coverage: `eval` package floor 95 % maintained; `paired.py` targeted at
  100 % (pure code, no excuse).

## Acceptance criteria

- [ ] Feature off by default → `mangomas eval` single-report path unchanged
  (test proves `eval.paired` is not imported).
- [ ] `mangomas eval compare` produces a `paired` payload and the documented
  exit codes (tests prove 0 / 2 / 3).
- [ ] Ternary variance, lower-bound gate, group bootstrap each mutation-proved.
- [ ] `ruff`, `mypy`, `pytest` (95 % gate), `frontmatter-lint` all clean.
- [ ] `docs/eval/harness.md` section added; `AGENTS.md` config row added;
  `specs/README.md` row added; CHANGELOG updated.
