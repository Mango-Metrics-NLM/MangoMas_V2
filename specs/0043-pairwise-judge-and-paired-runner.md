# Spec-0043: Pairwise judge and paired runner

- **Status:** Draft — threshold blocked on spec-0040 (judge calibration)
- **Linked ADR:** ADR-0041 (pairwise judgement is a separate protocol, not a `Scorer`)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added`
- **Delivery plan:** `docs/plans/20261027T220000Z-civ6-method-port-plan.md` (PR 3)
- **Depends on:** spec-0042 (its output is a `PairedSummary`)

## Problem

Spec-0042 compares two configurations through their *independent* scores.
For open-ended outputs — an `ExecutionPlan`, a `ReviewResult`, a chat
answer — no single-answer scorer is discriminating enough, and the
established alternative is a judge that sees both answers. That judge has a
known, measured failure: position bias. `llm_judge.py` today makes one
call with a fixed `PREDICTION` / `EXPECTED` layout and cannot express a
comparison at all; its `Scorer` protocol (`score(prediction, expected)`) is
single-sided by design. Bending `Scorer` to carry two predictions would
break every existing scorer's contract for one new use. A separate
protocol is the honest shape.

## Requirements

- `eval/protocol.py` gains `PairwiseVerdict` (frozen: `winner:
  Literal["a","b","draw"]`, `confidence: float`, `rationale: str`,
  `metadata: dict`) and `PairwiseJudge` (`@runtime_checkable`; `name: str`;
  `async judge(prediction_a, prediction_b, *, request, context) -> PairwiseVerdict`).
  `Scorer` is unchanged.
- `eval/judges/llm_pairwise.py` registered as `"llm_pairwise"` in a new
  `pairwise_judge_registry` (`eval/registry.py`, same `Registry[T]`).
  It issues **two** LLM calls per row, the second with the answers swapped,
  and resolves:
  - consistent (A then A-after-swap, i.e. the same *answer* wins) → that
    winner, `confidence = min(c1, c2)`;
  - inconsistent (the *position* wins both times) → `draw`,
    `metadata["position_inconsistent"] = True`;
  - either reply malformed → `LLMBadResponse` (row errored, never scored 0 —
    the `llm_judge` rule).
  The swap is not a knob. A single-call mode is deliberately absent.
- `EvalRunner.run_paired(target_a, target_b, *, judge, dataset, ...) ->
  PairedReport`: both targets resolved through `target_registry` as today;
  the **same** `AgentRequest` object content goes to both; rows carry both
  predictions, both verdicts, the resolved outcome, and `duration_ms` /
  `cost_usd` per side. Judge cost is accounted as its own column
  (`judge_cost_usd`) so "equal budget" comparisons do not launder judge
  spend into target spend.
- `PairedReport` serialises through `_serialize.paired_report_payload`
  and feeds `paired.summarize_paired` directly (no re-pairing: rows are
  born paired).
- `mangomas eval paired --target-a <name> --target-b <name>
  [--target-a-options … --target-b-options …] --judge llm_pairwise
  [--group-key task_family] [--gate …]`; exit codes as spec-0042.
- `position_inconsistency_rate` is a first-class summary field; a gate
  option `max_position_inconsistency` (default `None`, off) fails the run
  when the judge is too biased to trust, *before* any winner is believed.
- Must remain **additive & default-OFF**: `mangomas eval` and
  `EvalRunner.run` unchanged; `llm_judge` scorer unchanged.

## Scenarios (WHEN/THEN)

- WHEN a `FakeLLM` always prefers the **first-listed** answer THEN every row
  is `draw` with `position_inconsistent=True` and
  `position_inconsistency_rate == 1.0` AND WHEN the fake prefers the
  **longer** answer regardless of position THEN the longer answer wins with
  `position_inconsistent=False`.
- WHEN the swapped call is removed (mutation) THEN the first scenario's
  test goes red — the proof that the swap is load-bearing.
- WHEN target B raises on a row THEN the row is `errored` with A's
  prediction retained in the row AND WHEN neither raises THEN the row is
  judged.
- WHEN the judge returns non-JSON THEN `LLMBadResponse` marks the row
  errored AND the run continues unless `fail_fast`.
- WHEN `max_position_inconsistency=0.2` and the measured rate is `0.5` THEN
  the gate fails with a verdict naming the judge, not the targets AND WHEN
  the rate is `0.1` THEN the gate proceeds to the score-rate verdict.
- WHEN `--target-a echo --target-b echo` with identical `text` THEN the test
  first asserts the predictions are identical and the summary reports
  `decisive_games == 0` (a tie must not produce a winner).
- WHEN `run_paired` is called THEN `run`'s behaviour on the same dataset is
  byte-identical to before this spec (snapshot test on `report_payload`).

## Config / env additions

| Env var (`MANGOMAS_*`) | Default | Purpose |
|------------------------|---------|---------|
| `MANGOMAS_EVAL__PAIRWISE_JUDGE` | `llm_pairwise` | Judge name resolved via `pairwise_judge_registry` |
| `MANGOMAS_EVAL__PAIRWISE_OPTIONS` | `{}` | Per-judge options keyed by judge name (`min_confidence`, `max_position_inconsistency`) |
| `MANGOMAS_EVAL__TARGET_B` | _(none)_ | Second target for `eval paired`; required by the subcommand, ignored elsewhere |
| `MANGOMAS_EVAL__TARGET_B_OPTIONS` | `{}` | Per-target options for the B side |

Constants: `DEFAULT_PAIRWISE_JUDGE = "llm_pairwise"`,
`DEFAULT_PAIRWISE_MIN_CONFIDENCE = 0.5` **(placeholder — `# calibrate:
spec-0040`; the value is a floor to be replaced by a measured one, and the
constant's docstring says so)**, `DEFAULT_PAIRWISE_MAX_POSITION_INCONSISTENCY
= None`.

## Protocol / contract impact

- New protocols: `PairwiseJudge`, `PairwiseVerdict` in `eval/protocol.py`
  (ADR-0041). `Scorer` unchanged — a `PairwiseJudge` is **not** a `Scorer`
  and the registry refuses a factory that satisfies the wrong protocol
  (`Registry[T]` protocol check).
- New error types: _none_ — judge failures are `LLMBadResponse`; config
  misuse is `ConfigError`.
- Registry additions: `pairwise_judge_registry` with provider key
  `llm_pairwise`; discovery group `mangomas.eval.pairwise_judges` under the
  existing `MANGOMAS_DISCOVERY_ENABLED` flag and the same built-in-override
  refusal (ADR-0030).

## Backwards-compatibility

- `run`, every scorer, every sink, `report_payload`: byte-identical
  (snapshot test).
- `EvalReport` is not extended; `PairedReport` is a sibling type so
  `baseline.load_baseline` keeps round-tripping only `EvalReport`.

## Test plan

- Unit: `tests/eval/test_pairwise_judge.py` (position-bias fake, swap
  mutation proof, malformed reply), `tests/eval/test_paired_runner.py`
  (identical request to both sides, B-fails-A-kept, echo tie, cost
  columns), `tests/eval/test_cli_paired.py` (exit codes). `FakeLLM` gains
  a **scripted pairwise mode** in `tests/fakes.py` (first-position /
  longer-answer / malformed) — do not add `embed()` to `FakeLLM`.
- Hypothesis: for any pair of verdict confidences, the resolved confidence
  is `min`; swapping (A, B) → (B, A) flips the winner and preserves draws.
- Gated: none — no external SDK.
- Gates: both directions per scenario; mutation proofs: remove swap;
  score malformed as 0 instead of raising; let judge cost leak into
  target cost.
- Coverage: `eval` 95 % floor maintained.

## Acceptance criteria

- [ ] Feature off by default → `run` / `report_payload` snapshot unchanged.
- [ ] `eval paired` with the echo fixture produces a `PairedReport`,
  summarised by spec-0042's functions, with documented exit codes.
- [ ] Position bias is detected and neutralised (two-direction test;
  swap-removal mutation proof named in PR body).
- [ ] `DEFAULT_PAIRWISE_MIN_CONFIDENCE` carries the `calibrate: spec-0040`
  marker and a guard test fails if the marker is removed without a
  recorded measurement in `docs/analysis/`.
- [ ] `ruff`, `mypy`, `pytest` (95 % gate), `frontmatter-lint` all clean.
- [ ] CHANGELOG updated; ADR-0041 added; `AGENTS.md` config rows added.
