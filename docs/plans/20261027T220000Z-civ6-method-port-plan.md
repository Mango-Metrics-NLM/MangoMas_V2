# Civ6-AlphaZero method port — delivery plan

- **Branch:** `feat/paired-eval` (each PR from the current integration branch, restarted per PR)
- **Date:** 2026-10-27
- **Target release:** rolling (post-0.5.0; does not pre-empt Sprint 1 of the 2026-10-28 plan)
- **Status:** Draft — awaiting user decision on numbering and ordering
- **Specs:** spec-0042 (paired statistics), spec-0043 (pairwise judge + paired runner), spec-0044 (decision records), spec-0045 (plan-search spike, kill-criterion only)
- **ADRs:** ADR-0041 (pairwise judgement is a separate protocol, not a `Scorer`), ADR-0042 (decision records are a third stream, separate from turns and cognitive signals)

## Executive summary

The 2026-10-27 peer review of the Civ6→MangoMas report established, against
both repositories, that the only transferable *algorithmic* asset is Civ6's
evaluation statistics (`alpha_zero/evaluation/scoring.py`: score rate with
draws at 0.5, ternary variance, promotion-as-decision) and that MangoMas's
eval harness has **no** paired comparison at all — seven single-answer
scorers, `diff_reports` with absolute deltas and no interval, and an
`llm_judge` with no order swap. Everything else the report proposed is either
already present here (`mango-mutation-proof` is Civ6's negative-control
discipline; `tests/deploy/test_ci_make_parity.py` is its suite-wiring gate),
already fixed in Civ6 (backup sign, safe checkpoint load), or defective as
proposed (the HF Space `ResourceGridEnv.clone()` is unseeded).

Ships in four PRs, strictly in this order: **pure paired statistics first**
(spec-0042) because every later decision — including whether MCTS over plans
is worth anything — is made with that statistic; **decision records second**
(spec-0044) because it is independent of 0043 and its first emission point is
the eval runner, which 0042 already touches; **pairwise judging third**
(spec-0043) because it needs 0042's statistics and spec-0040's judge
calibration to set a threshold; **the plan-search spike last** (spec-0045)
because its only output is a measured verdict against a kill criterion, and
no `planning/` package lands unless it survives. The one constraint shaping
the order: nothing here may touch `core/agent.py` or the orchestrator client
while the 2026-10-28 plan serialises B2 → B3 → B1 on that file, so every
hook lands on `AgentContext.extras` or inside `eval/`.

## Numbering — confirm before implementation

The 2026-10-28 plan reserves spec-0039 (pass^k), spec-0040 (judge
calibration), spec-0041 (turn-record identity) and ADR-0038/0039, and names
ADR-0040 as the re-opening key for orchestrator decomposition. This plan
takes **spec-0042–0045** and **ADR-0041/0042**. If any of the reserved
numbers are abandoned, do not renumber this plan into the gap; the
`specs/README.md` "next free" line and the numbering guard (P5-B) are the
source of truth.

## Conventions binding every PR

Identical to the 2026-10-28 plan: additive, default-OFF;
`BREAKING-CHANGE: <exact path> — <why>` trailer per protected path — exactly
**one** is expected in this plan: PR 2 adds an import-linter contract for
`mangomas.decisions` to `pyproject.toml`, which is on the governance
surface (ADR-0030), so that commit carries `BREAKING-CHANGE: pyproject.toml
— import-linter contract for mangomas.decisions`. No PR touches
`core/agent.py` or the orchestrator client; every hook lands on
`AgentContext.extras` or inside `eval/`; every new guard mutation-proved (break → named test red →
restore, named in the PR body); domain literals in `tests.constants`; no
mocking of internal protocols; `make gate` + `make gated-suites` green; one
`CHANGELOG.md [Unreleased]` block per PR; ≤ ~600 changed lines; docs updated
in the same PR; specs/ADRs from the templates with the `specs/README.md` row.

One convention specific to this plan, inherited from Civ6 §5.2 (2026-09-04):
**any test that asserts on an argmax, a winner, or a gate verdict must first
prove its input is not a tie.** A paired test whose A and B are identical
passes for the wrong reason.

## PR 1 — paired evaluation statistics (spec-0042)

Pure functions over two existing `EvalReport`s. No new runtime surface, no
LLM, no new settings beyond the `compare` subcommand's options.

### Milestone 1.0 — ternary score statistics

- **Failing test first:** `test_interval_width_is_ternary_not_binomial` —
  a 142/588/70 (win/draw/loss) pair must yield a narrower interval than the
  binomial-on-fractional-successes formula, by exactly `draw_rate/4` in
  variance. Red until `score_variance` exists.
- **Depends on:** nothing — parallel-safe.
- `src/mangomas/eval/paired.py`: `score_rate`, `score_variance`,
  `score_interval`, `effective_n`, `PairedSummary`. The arithmetic mirrors
  Civ6 `evaluation/scoring.py:79-170` (MIT, same author); the code is
  rewritten, not vendored — MangoMas must stay numpy-free in `eval/`.
- `tests/constants/eval.py`: the 142/588/70 triple and the expected
  variance, so the literal appears once.

### Milestone 1.1 — pairing two reports

- **Failing test first:** `test_pair_reports_drops_unmatched_rows_and_reports_them`
  (red: no `pair_reports`). Second test, both directions:
  `test_zero_overlap_is_a_config_error_not_a_vacuous_pass`.
- **Depends on:** 1.0.
- `pair_reports(a, b, *, draw_epsilon) -> PairedRows`: match by `row_id`,
  classify each pair as `a` / `b` / `draw` from the per-row `score`,
  exclude rows errored on either side (count them, they reduce
  `effective_n`), surface `dropped`/`new` like `diff_reports`.
- `DEFAULT_PAIRED_DRAW_EPSILON` in `config/evaluation.py`, surfaced via
  `EvalSettings.paired_options`.

### Milestone 1.2 — family-clustered bootstrap

- **Failing test first:** `test_family_bootstrap_is_wider_than_row_bootstrap_when_families_are_correlated`
  — a fixture where every row in a family shares its outcome must produce a
  wider family-resampled interval than a row-resampled one. Red until the
  cluster resampler exists; this is the test that proves the grouping does
  something.
- **Depends on:** 1.1.
- `bootstrap_interval(rows, *, group_key="task_family", resamples, seed)`
  resamples **families** (from `EvalRowResult.metadata[group_key]`, which
  the runner already copies from `DatasetRow.metadata`), not rows. Rows with
  no family form a singleton family each and the result records
  `ungrouped_rows` so a dataset that forgot the key is visible, not silently
  row-bootstrapped.
- `DEFAULT_PAIRED_BOOTSTRAP_RESAMPLES`, `DEFAULT_PAIRED_BOOTSTRAP_SEED`
  (`random.Random`, stdlib — determinism gated by a two-run equality test,
  the Civ6 "diff two seeded runs" method).

### Milestone 1.3 — gate + CLI

- **Failing test first:** `test_paired_gate_fires_when_b_is_worse` and
  `test_paired_gate_passes_when_interval_excludes_regression` (both
  directions, per TEMPLATE).
- **Depends on:** 1.2.
- `evaluate_paired_gate(summary, *, threshold, confidence) -> GateResult`
  — pure, combinable via `merge_gate_results`; verdict rule is Civ6's
  `score_rate_lower_bound` (lower CI bound ≥ threshold), not point estimate.
- `mangomas eval compare --a <report.json> --b <report.json> [--gate]
  [--group-key task_family]`: reads through `baseline.load_baseline` so the
  payload shape stays the one `report_payload` writes; writes
  `paired_payload` to `--out`; exit 3 on gate failure after the payload is
  written, 2 on zero overlap.
- `docs/eval/harness.md`: one "Paired comparison" section; `AGENTS.md`
  config table gains the `MANGOMAS_EVAL__PAIRED_OPTIONS` row (the
  `test_env_example_contract.py` both-directions check will fail otherwise).

## PR 2 — decision records (spec-0044, ADR-0042)

### Milestone 2.0 — contract

- **Failing test first:** `test_record_carrying_both_predicted_and_observed_fails_validation`
  and `test_flag_off_leaves_extras_byte_identical`.
- **Depends on:** the 2026-10-28 plan's Sprint 1 import-linter PR
  (ADR-0039) being merged — both edit `[tool.importlinter]` in
  `pyproject.toml`, and a protected-path conflict is the worst kind to
  resolve by hand. Otherwise parallel-safe with PR 1; lands second to keep
  the eval runner diff serialised.
- `src/mangomas/decisions/`: `DecisionRecord` (Pydantic v2, frozen,
  `extra="forbid"`, `schema_version="1.0.0"`), `DecisionRecordSink`
  protocol hung on `AgentContext.extras["decision_sink"]` — the spec-0030
  pattern, so `core/agent.py` is untouched. JSONL sink only.
- `MANGOMAS_DECISIONS__*` settings group, `ENABLED=false`.
- `pyproject.toml` `[tool.importlinter]`: a `forbidden` contract —
  `mangomas.decisions` must not import `eval`, `cognitive`, `workflow`,
  `rag`, `agents`, `api`, or `core.orchestrator`. `decisions` is **not**
  added to the existing independence contract, because `eval` will import
  it (lazily, flag-on only) in 2.1. This is the plan's one protected-path
  edit; trailer as in Conventions. `cognitive/pdp.py`'s refuse-list gains
  the record field names as string literals in `cognitive/constants.py`
  so `cognitive` stays independent of `decisions`.

### Milestone 2.1 — first emission point: the eval runner

- **Failing test first:** `test_eval_run_with_flag_on_emits_one_observed_record_per_row`.
- **Depends on:** 2.0; PR 1 merged (shared `runner.py` edits).
- `EvalRunner` emits an `observed` record per scored row (state digest =
  hash of `messages` + target name + options; never raw content). The
  orchestrator-dispatch `predicted` emission point is **deferred** until the
  2026-10-28 plan's B1 lands on the orchestrator client — recorded under
  Deferred below so it is re-opened by decision.

## PR 3 — pairwise judge and paired runner (spec-0043, ADR-0041)

### Milestone 3.0 — `PairwiseJudge` protocol + order-swapped LLM judge

- **Failing test first:** `test_position_biased_judge_yields_draw_and_is_flagged`
  — a `FakeLLM` that always prefers the first-listed answer must produce
  `draw` with `position_inconsistent=True`; and the mirror
  `test_consistent_judge_yields_winner`. Mutation-proof: remove the second
  (swapped) call and the first test goes red.
- **Depends on:** nothing in code; **threshold choice blocked on spec-0040**
  (judge calibration) — lands with `DEFAULT_PAIRWISE_MIN_CONFIDENCE` at a
  conservative value and a `# calibrate: spec-0040` marker, not a guess
  dressed as a measurement.
- `eval/protocol.py` gains `PairwiseJudge` / `PairwiseVerdict`; `Scorer` is
  unchanged (ADR-0041). `eval/judges/llm_pairwise.py` registered in a new
  `pairwise_judge_registry`. Two judge calls per row, always.

### Milestone 3.1 — paired runner

- **Failing test first:** `test_paired_run_uses_identical_request_for_both_targets`
  and `test_target_b_failure_errors_the_row_and_keeps_a`.
- **Depends on:** 3.0, PR 1 (its output is a `PairedSummary`).
- `EvalRunner.run_paired(target_a, target_b, *, judge)` → `PairedReport`
  (rows carry both predictions, both judge calls, the verdict). Targets are
  resolved through `target_registry` exactly as today; `echo` with two
  different `text` options is the deterministic fixture. `cost_budget`
  metadata counts the two judge calls so budget-equal comparisons are
  honest.
- `mangomas eval paired --target-a … --target-b … --judge llm_pairwise`.

## PR 4 — plan-search spike (spec-0045)

### Milestone 4.0 — measured verdict, nothing else

- **Failing test first:** none — this PR adds no gated code. Its artifact
  is `docs/analysis/<date>-plan-search-spike.md` with the paired result.
- **Depends on:** PR 1 and PR 3 merged (the statistic and the judge);
  spec-0040 calibration applied.
- Three candidates at **equal measured budget** (tokens + tool calls, via
  the `cost_budget` basis): best-of-N (N=4), beam (width 2 / depth 2), and
  UCT over planner proposals with the reviewer as the value signal — all
  planner/reviewer/chat only, never `tool` (INV-16; the harness disposes).
  Code lives under `examples/spikes/plan_search/`, outside `[tool.coverage]
  source`, and imports only public `mangomas` surfaces.
- **Kill criterion (pre-registered here):** search ships as a feature spec
  only if its score-rate lower bound beats best-of-N on **both** task
  families at the same budget. Otherwise the analysis doc records the loss
  and this plan closes with no `planning/` package. The reviewer's sign is
  explicitly modelled as cooperative (no min-node) and the doc must say so.

## Deferred / out of scope

- **`ResourceGridEnv` as a test fixture** (report's recommendation): rejected.
  It lives only in the HF Space `ianshank/civ6-alphazero-mcts`, not in the
  repo, and its `clone()` builds an unseeded `np.random.RandomState()` — the
  same defect Civ6 fixed in `MicroCivEnv.clone()` on 2026-08-30. Re-opening
  requires seeding it first and a reason to prefer it over `echo`.
- **Elo / TrueSkill ratings**: rejected for 2–3 configurations; paired
  score rate with a clustered interval is sufficient and has no
  transitivity assumption. Re-open only with ≥ 5 concurrently-compared
  configurations (spec-0042 records the threshold).
- **Hex encoder, MuZero weights, distributed self-play**: not applicable;
  nothing to port.
- **Porting Civ6's negative-control / suite-wiring gates**: already here as
  `mango-mutation-proof` and `tests/deploy/test_ci_make_parity.py`. The
  one Civ6 method not yet present — a seeded determinism gate proved by
  diffing two runs — is the 2026-10-28 plan's P3-A; this plan's 1.2 uses the
  same method for the bootstrap and does not duplicate P3-A.
- **`predicted` decision records at orchestrator dispatch**: deferred until
  B1 (orchestrator client) lands; re-open via spec-0044 milestone 2.2.
- **Learned routing in shadow mode** (`routing.recommendation`): requires
  decision records with both labels populated over a real volume; nothing
  to build until 2.2 exists and has data. No spec number reserved.
- **Judge calibration**: spec-0040 (reserved by the 2026-10-28 plan), not
  this plan.

## Verification

```bash
make gate                                   # full pre-PR chain, CI's order
make gated-suites
# PR 1
python -m pytest tests/eval/test_paired.py tests/eval/test_cli_compare.py --no-cov -q
# PR 2
python -m pytest tests/decisions tests/eval/test_runner_decision_records.py --no-cov -q
# PR 3
python -m pytest tests/eval/test_pairwise_judge.py tests/eval/test_paired_runner.py --no-cov -q
# Mutation proofs named in each PR body — see .claude/skills/mango-mutation-proof
```
